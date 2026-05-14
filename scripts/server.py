"""HTTP API for the geocode + fetch pipeline.

Wraps geocode.py and fetch.py as JSON endpoints. Includes in-process
request coalescing so two concurrent requests for the same place share
one Overpass call instead of firing duplicate requests.

Endpoints:
  GET  /api/layers           - returns layers.json (the registry)
  POST /api/place            - {"query": "Lisbon"} -> Anchor
  POST /api/data             - {"anchor": {...}, "layers": [...]} -> per-layer data
  GET  /api/health           - liveness + cache stats
  GET  /docs                 - auto-generated OpenAPI UI

Run:
  uv run python server.py                 # localhost:8000
  uv run python server.py --reload        # auto-reload on file changes
  uv run python server.py --port 5000

Dependencies (run once):
  uv add fastapi 'uvicorn[standard]'
"""

import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Optional

import orjson
from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, ConfigDict, Field

import db
import fetch as fetch_mod
import presets_db
from fetch import (
    DEFAULT_RADIUS_KM,
    MAX_RADIUS_KM,
    MIN_RADIUS_KM,
    anchor_at_radius,
)
from geocode import Anchor, geocode


# ---------- request coalescing ----------
#
# When two clients ask for the same place simultaneously, we don't want
# both to hit Overpass — the second should ride on the first's result.
# Keyed by query string (geocode) or anchor+layers (fetch). Cleared as
# soon as the underlying call resolves.

_geocode_inflight: dict[str, asyncio.Future] = {}
_fetch_inflight: dict[str, asyncio.Future] = {}


# ---------- request/response models ----------

# Reusable example payloads — referenced by both Pydantic schemas (for the
# "Schema" tab) and FastAPI Body() openapi_examples (for the "Example value"
# dropdown with multiple labelled cases).

EXAMPLE_PLACE_SIMPLE = {"query": "Lisbon"}
EXAMPLE_PLACE_SUBPLACE = {"query": "Cerro Alegre, Valparaíso"}
EXAMPLE_PLACE_OVERRIDES = {
    "query": "Reykjavík",
    "max_extent_km": 100,
    "fallback_radius_km": 12,
}

EXAMPLE_ANCHOR_LISBOA = {
    "osm_id": 5400890,
    "osm_type": "relation",
    "name": "Lisboa",
    "display_name": "Lisboa, Portugal",
    "level": "city",
    "lat": 38.7077507,
    "lon": -9.1365919,
    "bbox": [38.6913994, -9.2298356, 38.7967584, -9.0863328],
    "extent_km": 12.46,
    "bbox_synthesized": False,
}

EXAMPLE_DATA_FEW_LAYERS = {
    "anchor": EXAMPLE_ANCHOR_LISBOA,
    "layers": ["road_motorway", "road_primary", "coastline"],
    "radius_km": 30,
    "force": False,
}
EXAMPLE_DATA_ALL_LAYERS = {
    "anchor": EXAMPLE_ANCHOR_LISBOA,
    "radius_km": 30,
    "force": False,
}
EXAMPLE_DATA_FORCE = {
    "anchor": EXAMPLE_ANCHOR_LISBOA,
    "layers": ["building"],
    "radius_km": 40,
    "force": True,
}


class PlaceRequest(BaseModel):
    query: str = Field(
        ..., min_length=1, description="Free-text place name.",
        examples=["Lisbon", "Cerro Alegre, Valparaíso"],
    )
    max_extent_km: Optional[float] = Field(
        None, description="Override admin-bbox synthesis threshold (default 50).",
        examples=[50, 100],
    )
    fallback_radius_km: Optional[float] = Field(
        None, description="Half-extent of synthesized bbox (default 8).",
        examples=[8, 12],
    )

    model_config = ConfigDict(
        json_schema_extra={"example": EXAMPLE_PLACE_SIMPLE}
    )


class AnchorPayload(BaseModel):
    osm_id: int
    osm_type: str
    name: str
    display_name: str
    level: str
    lat: float
    lon: float
    bbox: tuple[float, float, float, float]
    extent_km: float
    bbox_synthesized: bool

    model_config = ConfigDict(
        json_schema_extra={"example": EXAMPLE_ANCHOR_LISBOA}
    )


class DataRequest(BaseModel):
    anchor: AnchorPayload
    layers: Optional[list[str]] = Field(
        None,
        description="Layer ids to fetch. Defaults to every layer.",
        examples=[["road_motorway", "coastline"]],
    )
    radius_km: int = Field(
        DEFAULT_RADIUS_KM,
        ge=MIN_RADIUS_KM,
        le=MAX_RADIUS_KM,
        description=(
            f"Half-extent of the fetch square in km. The fetch bbox is a "
            f"{2 * MIN_RADIUS_KM}-{2 * MAX_RADIUS_KM} km square centered on "
            "the anchor's lat/lon. Cache is keyed by this radius."
        ),
    )
    force: bool = Field(
        False, description="Bypass cache and re-fetch.",
    )

    model_config = ConfigDict(
        json_schema_extra={"example": EXAMPLE_DATA_FEW_LAYERS}
    )


# ---------- lifespan ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    presets_db.init_db()
    yield


app = FastAPI(
    title="CityMapFrames API",
    version="0.1",
    description="Geocode places + fetch layered OSM data with persistent cache.",
    # orjson is 5–10× faster than stdlib json on serialization. For
    # heavy layers (`building_roof` at 30 km can be 50+ MB of JSON),
    # this is the difference between a 2 s and a 200 ms response.
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

# Gzip — added BEFORE CORS so it sits on the OUTSIDE of the middleware
# stack (Starlette processes middleware LIFO on response). The middleware
# only fires when the client sends Accept-Encoding: gzip (every browser
# does by default).
#
# compresslevel=1 is deliberate: for JSON map payloads, level 1 produces
# bodies ~85-95% the size of level 9 while costing ~3-5× LESS CPU. We're
# trading 1-2% of bandwidth for a huge server-side win. A 15 MB Valparaíso
# response goes from 15 MB → ~2-3 MB on the wire with about 100-200 ms of
# CPU instead of the 500-1000 ms level 9 would burn.
#
# minimum_size=1500 skips small responses (health checks, /api/layers,
# /api/place) where the gzip header overhead isn't worth it.
app.add_middleware(GZipMiddleware, minimum_size=1500, compresslevel=1)

# CORS — defaults to wildcard for localhost dev.
# In production set CORS_ORIGINS to your actual frontend origin, e.g.:
#   CORS_ORIGINS=https://citymapframes.example.com
# Multiple origins: comma-separated.
_cors_origins_raw = os.environ.get("CORS_ORIGINS", "*")
_cors_origins = (
    ["*"] if _cors_origins_raw.strip() == "*"
    else [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- helpers ----------

async def _run_blocking(func, *args, **kwargs):
    """Run a blocking call (geocode/fetch) on the default thread pool so
    the event loop stays responsive."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


def _coalesce(table: dict[str, asyncio.Future], key: str):
    """Return (existing_future, None) if a request is already in flight,
    or (None, new_future) if this caller owns the work."""
    if key in table:
        return table[key], None
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    table[key] = fut
    return None, fut


# ---------- response examples ----------

EXAMPLE_LAYERS_RESPONSE = {
    "version": 1,
    "categories": {
        "roads": {"label": "Roads"},
        "water": {"label": "Water"},
    },
    "render_order": ["coastline", "road_primary", "road_motorway"],
    "layers": [
        {
            "id": "road_motorway",
            "category": "roads",
            "kind": "line",
            "selector": "way[\"highway\"=\"motorway\"]",
            "match": [{"key": "highway", "value": "motorway"}],
            "default_visible": True,
            "heavy": False,
            "style": {"stroke": "#e74c3c", "width": 4},
        }
    ],
}

EXAMPLE_DATA_RESPONSE = {
    "anchor": EXAMPLE_ANCHOR_LISBOA,
    "layers": {
        "road_motorway": [
            {
                "type": "way",
                "id": 12345678,
                "tags": {"highway": "motorway", "ref": "A1"},
                "geometry": [
                    {"lat": 38.71, "lon": -9.15},
                    {"lat": 38.72, "lon": -9.14},
                ],
            }
        ],
        "coastline": [],
    },
}


# ---------- endpoints ----------

@app.get(
    "/api/layers",
    summary="Get the layer registry",
    responses={
        200: {
            "description": "Full layer registry (layers.json contents).",
            "content": {"application/json": {"example": EXAMPLE_LAYERS_RESPONSE}},
        }
    },
)
async def get_layers():
    """Return the full `layers.json` (registry of every available layer)."""
    return fetch_mod.load_layers()


@app.post(
    "/api/place",
    summary="Geocode a place name to an anchor",
    responses={
        200: {
            "description": "Resolved anchor.",
            "content": {"application/json": {"example": EXAMPLE_ANCHOR_LISBOA}},
        },
        404: {
            "description": "No geocoding result.",
            "content": {"application/json": {"example": {
                "detail": "No geocoding result for: 'asdfqwerty'"
            }}},
        },
    },
)
async def post_place(
    req: PlaceRequest = Body(
        ...,
        openapi_examples={
            "simple": {
                "summary": "Simple place name",
                "description": "Most common case — a single city name.",
                "value": EXAMPLE_PLACE_SIMPLE,
            },
            "subplace": {
                "summary": "Sub-place that climbs to its city parent",
                "description": (
                    "A neighbourhood is resolved to its city anchor so cache "
                    "stays shared across all queries inside the same city."
                ),
                "value": EXAMPLE_PLACE_SUBPLACE,
            },
            "with_overrides": {
                "summary": "Custom bbox synthesis thresholds",
                "description": (
                    "Useful for places whose admin polygon is awkward "
                    "(e.g. islands or unusual jurisdictions)."
                ),
                "value": EXAMPLE_PLACE_OVERRIDES,
            },
        },
    )
):
    """Resolve a place query to a stable parent Anchor (osm_id + bbox).

    Concurrent identical queries are coalesced — only one Nominatim call
    is made, every caller gets the same result.
    """
    key = req.query.strip().lower()
    existing, fut = _coalesce(_geocode_inflight, key)
    if existing is not None:
        return await existing

    try:
        kwargs: dict = {}
        if req.max_extent_km is not None:
            kwargs["max_extent_km"] = req.max_extent_km
        if req.fallback_radius_km is not None:
            kwargs["fallback_radius_km"] = req.fallback_radius_km

        anchor = await _run_blocking(geocode, req.query, **kwargs)
        result = asdict(anchor)
        fut.set_result(result)
        return result
    except ValueError as e:
        # No geocoding result — client error, not server error.
        fut.set_exception(e)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        fut.set_exception(e)
        raise HTTPException(status_code=500, detail=f"geocode failed: {e}")
    finally:
        _geocode_inflight.pop(key, None)


@app.post(
    "/api/data",
    summary="Fetch per-layer OSM data for an anchor",
    responses={
        200: {
            "description": "Per-layer OSM elements ready for rendering.",
            "content": {"application/json": {"example": EXAMPLE_DATA_RESPONSE}},
        },
        400: {
            "description": "Unknown layer id.",
            "content": {"application/json": {"example": {
                "detail": "unknown layer id(s): ['no_such_layer']"
            }}},
        },
    },
)
async def post_data(
    req: DataRequest = Body(
        ...,
        openapi_examples={
            "few_layers": {
                "summary": "Fetch a small subset of layers",
                "description": (
                    "Common during iteration — pull only a few layers and "
                    "render. All four are likely cached after the first run."
                ),
                "value": EXAMPLE_DATA_FEW_LAYERS,
            },
            "all_layers": {
                "summary": "Fetch every layer (omit `layers`)",
                "description": (
                    "Defaults to every layer in the registry. First run "
                    "for a city: ~15-30 s. Subsequent runs: cache-fast."
                ),
                "value": EXAMPLE_DATA_ALL_LAYERS,
            },
            "force_refetch": {
                "summary": "Force re-fetch (skip cache)",
                "description": "Set `force=true` to bypass cached data.",
                "value": EXAMPLE_DATA_FORCE,
            },
        },
    )
):
    """Pull every requested layer for the given anchor. Cache-first.

    Returns:
      {
        "anchor": {...},  # bbox is the synthesized square at radius_km
        "layers": { "<layer_id>": [<element>, ...], ... }
      }

    The fetch bbox is always a synthesized square at `radius_km` around
    the anchor's centroid — `anchor.bbox` from the request is ignored.
    Concurrent requests for the same (anchor, layers, radius_km, force)
    tuple are coalesced.
    """
    a = req.anchor
    raw_anchor = Anchor(
        osm_id=a.osm_id, osm_type=a.osm_type,
        name=a.name, display_name=a.display_name,
        level=a.level, lat=a.lat, lon=a.lon,
        bbox=tuple(a.bbox), extent_km=a.extent_km,
        bbox_synthesized=a.bbox_synthesized,
    )
    # The fetch operates on a radius-adjusted copy. The response carries
    # this same anchor so the renderer projects from the correct bbox.
    fetch_anchor = anchor_at_radius(raw_anchor, req.radius_km)

    layer_ids = req.layers or fetch_mod.all_layer_ids()

    # Validate layer ids early — better than a confusing KeyError mid-fetch.
    known = set(fetch_mod.all_layer_ids())
    unknown = [lid for lid in layer_ids if lid not in known]
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"unknown layer id(s): {unknown}"
        )

    coalesce_key = (
        f"{a.osm_type}/{a.osm_id}/"
        f"{','.join(sorted(layer_ids))}/r={req.radius_km}/force={req.force}"
    )
    existing, fut = _coalesce(_fetch_inflight, coalesce_key)
    if existing is not None:
        # Coalesced caller — the primary stored the already-encoded JSON
        # bytes in the future, splice them into a fresh Response.
        body = await existing
        return Response(content=body, media_type="application/json")

    try:
        data = await _run_blocking(
            fetch_mod.fetch, raw_anchor, layer_ids, req.force, req.radius_km
        )
        result = {
            "anchor": asdict(fetch_anchor),
            "layers": {lid: d["elements"] for lid, d in data.items()},
        }
        # IMPORTANT: explicitly construct ORJSONResponse here. Returning
        # a bare dict makes FastAPI run jsonable_encoder over the whole
        # structure first — for 30+ MB of nested geometry that's a
        # 6-second pre-walk. With an explicit response we go straight to
        # orjson.dumps and pay only the encode cost (~150 ms).
        resp = ORJSONResponse(content=result)
        # Cache the encoded bytes for any coalesced waiters so they don't
        # have to re-encode the same dict.
        fut.set_result(resp.body)
        return resp
    except Exception as e:
        fut.set_exception(e)
        raise HTTPException(status_code=500, detail=f"fetch failed: {e}")
    finally:
        _fetch_inflight.pop(coalesce_key, None)


@app.get(
    "/api/health",
    summary="Liveness + cache stats",
    responses={
        200: {
            "content": {"application/json": {"example": {
                "ok": True,
                "db_path": "cache.db",
                "anchors": 2,
                "geocoded_queries": 4,
                "cached_layers": 39,
                "total_elements": 41613,
                "db_size_bytes": 4468736,
                "db_size_mb": 4.26,
            }}}
        }
    },
)
async def health():
    return {"ok": True, **db.stats()}


# ---------- /api/share — sharable designs ----------

# Caps. Tighten with slowapi rate-limiting before deploying publicly.
MAX_DESIGN_BYTES = 8 * 1024  # ~8 KB
MAX_NAME_LEN = 80
MAX_ID_LEN = 16  # generous over the actual 8-char generator


class SharedAnchor(BaseModel):
    osm_type: str
    osm_id: int
    lat: float
    lon: float


class StyleOverridePayload(BaseModel):
    """Permissive — extra unknown keys are ignored on the server.
    The frontend's LayerStyleOverride is the canonical type."""
    model_config = ConfigDict(extra="ignore")
    stroke: Optional[str] = None
    fill: Optional[str] = None
    background: Optional[str] = None
    width: Optional[float] = None


class ViewPayload(BaseModel):
    centerLat: float
    centerLon: float
    zoom: float


class SharedDesign(BaseModel):
    """Mirrors web/src/lib/types.ts → SharedDesign. Schema-versioned for
    forward compat — bump the integer when a structural change lands.

    Two share kinds, distinguished by the presence of place fields:
      * Full   — has `query` + `anchor` + `radiusKm`. Recipient sees the
                 exact map (refetches geometry).
      * Style  — only `enabledLayers` + `overrides`. Recipient applies
                 them to their current map without refetching.
    """
    model_config = ConfigDict(extra="ignore")
    schemaVersion: int = Field(1, ge=1)
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LEN)

    # ---- design (always present) ----
    enabledLayers: list[str] = Field(default_factory=list, max_length=200)
    overrides: Optional[dict[str, StyleOverridePayload]] = None

    # ---- place + viewport (present only in "full" shares) ----
    query: Optional[str] = Field(None, min_length=1, max_length=200)
    anchor: Optional[SharedAnchor] = None
    radiusKm: Optional[int] = Field(None, ge=MIN_RADIUS_KM, le=MAX_RADIUS_KM)
    view: Optional[ViewPayload] = None


class ShareCreateRequest(BaseModel):
    design: SharedDesign
    parent_id: Optional[str] = Field(None, max_length=MAX_ID_LEN)


class ShareCreateResponse(BaseModel):
    id: str
    name: str
    parent_id: Optional[str] = None
    created_at: int
    deduped: bool = False


class ShareGetResponse(BaseModel):
    id: str
    name: str
    design: SharedDesign
    parent_id: Optional[str] = None
    view_count: int
    created_at: int


class ShareListResponse(BaseModel):
    items: list[ShareGetResponse]


EXAMPLE_FULL_DESIGN = {
    "schemaVersion": 1,
    "name": "Lisbon, dim buildings",
    "query": "Lisbon",
    "anchor": {
        "osm_type": "relation", "osm_id": 5400890,
        "lat": 38.7077507, "lon": -9.1365919,
    },
    "radiusKm": 15,
    "enabledLayers": ["coastline", "road_motorway", "road_primary", "building"],
    "overrides": {
        "building": {"fill": "#2a2f4a"},
        "road_motorway": {"stroke": "#ff7676", "width": 3},
    },
}

EXAMPLE_STYLE_ONLY_DESIGN = {
    "schemaVersion": 1,
    "name": "Sunset palette",
    "enabledLayers": ["coastline", "road_motorway", "road_primary", "building"],
    "overrides": {
        "building": {"fill": "#3a2a4a"},
        "road_motorway": {"stroke": "#ff7676", "width": 3},
        "coastline": {"fill": "#1a1a2e", "background": "#0b0820"},
    },
}


@app.post(
    "/api/share",
    summary="Create a shareable design",
    response_model=ShareCreateResponse,
    responses={
        201: {"description": "New share created."},
        200: {"description": "Existing share returned (deduped on content_hash)."},
        400: {"description": "Invalid schema."},
        413: {"description": "Design exceeds size limit."},
    },
)
async def post_share(
    request: Request,
    req: ShareCreateRequest = Body(
        ...,
        openapi_examples={
            "full": {
                "summary": "Full design — place + view + style",
                "description": (
                    "Recipient sees the exact map you sent. Includes "
                    "anchor + radius + view, so the recipient's frontend "
                    "fetches geometry for that specific place."
                ),
                "value": {"design": EXAMPLE_FULL_DESIGN},
            },
            "style_only": {
                "summary": "Style only — layer selection + colors",
                "description": (
                    "Recipient applies the design to whatever map they "
                    "have loaded. No place data is sent, no refetch on "
                    "their side. The common case."
                ),
                "value": {"design": EXAMPLE_STYLE_ONLY_DESIGN},
            },
            "remix": {
                "summary": "Remix of an existing share",
                "value": {
                    "design": EXAMPLE_FULL_DESIGN,
                    "parent_id": "k3Bz9aLw",
                },
            },
        },
    ),
):
    """Persist a SharedDesign. Anonymous and immutable: posters get a
    short id back; deletion / editing are not exposed in v1.

    Identical content (same `sha256(design)`) returns the existing id
    with HTTP 200 instead of inserting a duplicate.
    """
    name = req.design.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    design_dict = req.design.model_dump(exclude_none=True)
    serialized = orjson.dumps(design_dict)
    if len(serialized) > MAX_DESIGN_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"design exceeds {MAX_DESIGN_BYTES} bytes "
                f"({len(serialized)} given)"
            ),
        )

    client_ip = request.client.host if request.client else None

    result = await _run_blocking(
        presets_db.create_preset,
        name=name,
        design=design_dict,
        parent_id=req.parent_id,
        creator_ip=client_ip,
    )

    response = ShareCreateResponse(
        id=result["id"],
        name=result["name"],
        parent_id=result["parent_id"],
        created_at=result["created_at"],
        deduped=result["deduped"],
    )
    # 200 on dedup (we returned an existing share), 201 on fresh insert.
    status = 200 if result["deduped"] else 201
    return ORJSONResponse(content=response.model_dump(), status_code=status)


@app.get(
    "/api/share",
    summary="List recent shared designs",
    response_model=ShareListResponse,
    response_model_exclude_none=True,
)
async def list_shares(recent: int = 20, offset: int = 0):
    """Return the most recently posted designs, newest first.
    All shares are public in v1 — there's no listed/unlisted flag.

    Caps: `recent` is clamped server-side to [1, 100].
    """
    items = await _run_blocking(presets_db.list_recent, recent, offset)
    return {"items": items}


@app.get(
    "/api/share/{preset_id}",
    summary="Load a shared design",
    response_model=ShareGetResponse,
    response_model_exclude_none=True,
    responses={404: {"description": "Preset not found."}},
)
async def get_share(preset_id: str):
    if not preset_id or not preset_id.isalnum() or len(preset_id) > MAX_ID_LEN:
        raise HTTPException(status_code=400, detail="invalid preset id")
    result = await _run_blocking(presets_db.get_preset, preset_id, True)
    if result is None:
        raise HTTPException(status_code=404, detail="preset not found")
    return result


# ---------- CLI ----------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        prog="server.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run the CityMapFrames HTTP API.",
        epilog=(
            "Examples:\n"
            "  server.py                       # listen on http://127.0.0.1:8000\n"
            "  server.py --port 5000           # custom port\n"
            "  server.py --host 0.0.0.0        # expose on LAN (be careful)\n"
            "  server.py --reload              # dev mode, auto-restart on edits\n"
            "  server.py --db custom.db        # use a non-default cache DB\n"
            "\n"
            "Once running, browse http://127.0.0.1:8000/docs for the OpenAPI UI."
        ),
    )
    ap.add_argument("--host", default="127.0.0.1",
                    help="Interface to bind (default %(default)s).")
    ap.add_argument("--port", type=int, default=8000,
                    help="TCP port (default %(default)s).")
    ap.add_argument("--db", default=str(db.DEFAULT_DB_PATH), metavar="FILE",
                    help="SQLite cache path (default %(default)s).")
    ap.add_argument("--reload", action="store_true",
                    help="Auto-reload on source changes (dev only).")
    args = ap.parse_args()

    db.set_db_path(args.db)
    db.init_db()

    print(f"CityMapFrames API")
    print(f"  listening on http://{args.host}:{args.port}")
    print(f"  cache db:    {args.db}")
    print(f"  docs:        http://{args.host}:{args.port}/docs")
    print()

    import uvicorn
    if args.reload:
        # reload mode requires an import string so uvicorn can re-import.
        uvicorn.run(
            "server:app", host=args.host, port=args.port, reload=True
        )
    else:
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
