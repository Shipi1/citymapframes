"""Fetch OSM data per (anchor, layer) and cache it.

The orchestrator. Takes a geocoded Anchor + an optional list of layer IDs
from layers.json, returns assembled per-layer data ready for rendering.

Fetch strategy:
  - Skip layers already in cache (within TTL).
  - Group missing non-heavy layers into ONE Overpass union query.
  - Fetch each heavy layer in its own request, so a slow building query
    doesn't block roads/water from being usable.
  - Walk the response and route each element into per-layer cache cells
    using the `match` rules from layers.json.

Bbox: a square synthesized around the anchor's centroid at `radius_km`,
not the anchor's natural admin-polygon bbox. The slider in the frontend
only varies this radius; the anchor identity stays stable so cache rows
are reusable across queries to the same place.

Cache key: "{osm_type}/{osm_id}/{layer_id}/{radius_km}"
"""

import argparse
import json
import math
import random
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import requests

import db
from geocode import Anchor, geocode

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 300
USER_AGENT = "CityMapFrames/0.1 (personal map renderer; contact via github)"
LAYERS_FILE = Path(__file__).parent / "layers.json"

# Fetch scope is always a synthesized square — the slider in the UI just
# varies the radius. Defaults match the frontend's slider defaults.
DEFAULT_RADIUS_KM = 15
MIN_RADIUS_KM = 5
MAX_RADIUS_KM = 50


def square_bbox_around(lat: float, lon: float, radius_km: float):
    """Square (in km, not degrees) centered on (lat, lon), `radius_km` half-extent.

    Returned as (south, west, north, east). Compensates longitude for
    latitude scaling so the box is geographically square.
    """
    d_lat = radius_km / 111.32
    d_lon = radius_km / (111.32 * math.cos(math.radians(lat)))
    return (lat - d_lat, lon - d_lon, lat + d_lat, lon + d_lon)


def anchor_at_radius(anchor: Anchor, radius_km: int) -> Anchor:
    """Return a copy of `anchor` whose bbox is the synthesized square
    at `radius_km`. Identity (osm_id/osm_type/lat/lon) is unchanged."""
    bbox = square_bbox_around(anchor.lat, anchor.lon, radius_km)
    return replace(
        anchor,
        bbox=bbox,
        extent_km=2.0 * radius_km,
        bbox_synthesized=True,
    )


# ---------- layer registry ----------

_layers_data: dict | None = None


def load_layers() -> dict:
    global _layers_data
    if _layers_data is None:
        _layers_data = json.loads(LAYERS_FILE.read_text(encoding="utf-8"))
    return _layers_data


def get_layer(layer_id: str) -> dict:
    for layer in load_layers()["layers"]:
        if layer["id"] == layer_id:
            return layer
    raise KeyError(f"unknown layer: {layer_id}")


def all_layer_ids() -> list[str]:
    return [layer["id"] for layer in load_layers()["layers"]]


# ---------- match rules ----------

def types_from_selector(selector: str) -> set[str]:
    """Parse the element-type prefix of an Overpass selector.

    way[...]  -> {"way"}
    nwr[...]  -> {"node","way","relation"}
    nw[...]   -> {"node","way"}
    """
    prefix = selector.split("[", 1)[0]
    table = {"n": "node", "w": "way", "r": "relation"}
    return {table[c] for c in prefix if c in table}


def element_matches_layer(el: dict, layer: dict) -> bool:
    """Does this element belong in this layer? AND of all match conditions."""
    if el.get("type") not in types_from_selector(layer["selector"]):
        return False
    tags = el.get("tags") or {}
    for cond in layer["match"]:
        key = cond["key"]
        if key not in tags:
            return False
        val = tags[key]
        if "value" in cond and val != cond["value"]:
            return False
        if "value_not" in cond and val == cond["value_not"]:
            return False
    return True


# ---------- HTTP with retry ----------

def _retry_post(fn):
    def wrapper(*args, **kwargs):
        for attempt in range(6):
            resp = fn(*args, **kwargs)
            if resp.status_code in (429, 502, 503, 504):
                if attempt == 5:
                    resp.raise_for_status()
                delay = (2**attempt) * (0.7 + random.random() * 0.6)
                print(f"  Overpass {resp.status_code} - retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
            return resp
    return wrapper


_post = _retry_post(requests.post)


def overpass(ql: str) -> dict:
    resp = _post(
        OVERPASS_URL,
        data={"data": ql},
        timeout=OVERPASS_TIMEOUT + 30,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    if not resp.ok:
        # Overpass usually puts a useful error message in the body — surface it.
        body = (resp.text or "")[:600]
        print(f"\n[Overpass {resp.status_code}]\n{body}\n", file=sys.stderr)
        print(f"--- query that failed ---\n{ql}\n", file=sys.stderr)
        resp.raise_for_status()
    return resp.json()


# ---------- cache (thin wrappers around db.py) ----------

def cache_get(anchor: Anchor, layer_id: str, radius_km: int) -> dict | None:
    """Returns {"elements": [...]} or None on miss/stale."""
    elements = db.get_layer_cache(
        anchor.osm_type, anchor.osm_id, layer_id, radius_km
    )
    return None if elements is None else {"elements": elements}


def cache_set(
    anchor: Anchor, layer_id: str, radius_km: int, data: dict
) -> None:
    """data must have an 'elements' list."""
    db.put_layer_cache(
        anchor.osm_type, anchor.osm_id, layer_id, radius_km, data["elements"]
    )


# ---------- query building ----------

def _bbox_setting(anchor: Anchor) -> str:
    s, w, n, e = anchor.bbox
    return f"[bbox:{s:.6f},{w:.6f},{n:.6f},{e:.6f}]"


def union_query(anchor: Anchor, layers: list[dict]) -> str:
    parts = "\n  ".join(f"{layer['selector']};" for layer in layers)
    return (
        f"[out:json][timeout:{OVERPASS_TIMEOUT}]{_bbox_setting(anchor)};\n"
        f"(\n  {parts}\n);\n"
        f"out geom;"
    )


def single_query(anchor: Anchor, layer: dict) -> str:
    return (
        f"[out:json][timeout:{OVERPASS_TIMEOUT}]{_bbox_setting(anchor)};\n"
        f"{layer['selector']};\n"
        f"out geom;"
    )


# ---------- routing ----------

def route_to_layers(elements: list[dict], layers: list[dict]) -> dict[str, list[dict]]:
    """Walk a flat element list and bucket each into the layers it matches.

    An element can satisfy multiple layers (rare with our current registry,
    but possible if e.g. someone adds an overlapping selector). We append
    to every match.
    """
    per_layer: dict[str, list[dict]] = {layer["id"]: [] for layer in layers}
    for el in elements:
        for layer in layers:
            if element_matches_layer(el, layer):
                per_layer[layer["id"]].append(el)
    return per_layer


# ---------- main entry ----------

def fetch(
    anchor: Anchor,
    layer_ids: list[str] | None = None,
    force: bool = False,
    radius_km: int = DEFAULT_RADIUS_KM,
) -> dict[str, dict]:
    """Return {layer_id: {"elements": [...]}} for every requested layer.

    `radius_km` defines the fetch bbox: a square `2*radius_km` km on a
    side, centered on the anchor's lat/lon. The anchor's own `bbox`
    field is overridden — only its identity (osm_id/osm_type) and
    centroid (lat/lon) are used.

    Pulls from cache where possible; fetches missing layers in one
    union request (plus one extra request per heavy layer).
    """
    if layer_ids is None:
        layer_ids = all_layer_ids()

    # Replace the anchor's bbox with the synthesized square. All Overpass
    # queries below use `anchor.bbox`, so everything downstream just works.
    anchor = anchor_at_radius(anchor, radius_km)

    out: dict[str, dict] = {}
    missing: list[str] = []
    for lid in layer_ids:
        cached = None if force else cache_get(anchor, lid, radius_km)
        if cached is not None:
            out[lid] = cached
            print(f"  cached: {lid:30s} ({len(cached['elements'])} elements)")
        else:
            missing.append(lid)

    if not missing:
        return out

    layers = [get_layer(lid) for lid in missing]
    heavy = [layer for layer in layers if layer.get("heavy")]
    light = [layer for layer in layers if not layer.get("heavy")]

    # ----- union fetch for non-heavy layers -----
    if light:
        light_ids = [layer["id"] for layer in light]
        print(f"  union fetch: {len(light)} layers (r={radius_km}km)...")
        t = time.perf_counter()
        data = overpass(union_query(anchor, light))
        dt = time.perf_counter() - t
        print(f"    {len(data['elements'])} elements in {dt:.1f}s")
        per_layer = route_to_layers(data["elements"], light)
        for lid in light_ids:
            entry = {"elements": per_layer[lid]}
            cache_set(anchor, lid, radius_km, entry)
            out[lid] = entry
            print(f"    routed {lid:30s} {len(per_layer[lid])}")

    # ----- one query per heavy layer -----
    for layer in heavy:
        lid = layer["id"]
        print(f"  heavy fetch: {lid} (r={radius_km}km)...")
        t = time.perf_counter()
        data = overpass(single_query(anchor, layer))
        dt = time.perf_counter() - t
        # Apply match rules even here — handles selector vs. match drift
        # (e.g., building selector returns roofs, match excludes them).
        elements = [el for el in data["elements"] if element_matches_layer(el, layer)]
        print(f"    {len(data['elements'])} returned, {len(elements)} after match — {dt:.1f}s")
        entry = {"elements": elements}
        cache_set(anchor, lid, radius_km, entry)
        out[lid] = entry

    return out


# ---------- CLI ----------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        prog="fetch.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Fetch OSM data for a place and cache it per layer.\n"
            "Geocodes the place, then pulls every layer in layers.json (or a\n"
            "subset via --layers) using one Overpass union query plus one query\n"
            "per heavy layer."
        ),
        epilog=(
            "Examples:\n"
            "  fetch.py \"Lisbon\"\n"
            "  fetch.py \"Tokyo, Japan\" --out tokyo.json\n"
            "  fetch.py \"Valparaíso, Chile\" --layers road_motorway,road_primary,coastline\n"
            "  fetch.py \"Reykjavík\" --force\n"
            "\n"
            "Cache: ./place_cache.json keyed by (osm_type/osm_id/layer_id), TTL 14 days.\n"
            "Output: a JSON file with {anchor, layers: {id: [elements]}}.\n"
            "Run `node render.js OUTPUT_FILE` to draw the result.\n"
            "See MANUAL.md for full reference."
        ),
    )
    ap.add_argument("place", help="Place name (quoted if it has spaces).")
    ap.add_argument(
        "--layers",
        metavar="IDS",
        help=(
            "Comma-separated layer ids to fetch (e.g. road_motorway,building). "
            "Defaults to every layer in layers.json."
        ),
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Ignore the cache and re-fetch everything from Overpass.",
    )
    ap.add_argument(
        "--out",
        default="place_data.json",
        metavar="FILE",
        help="Where to write the assembled per-layer data (default %(default)s).",
    )
    ap.add_argument(
        "--radius",
        type=int,
        default=DEFAULT_RADIUS_KM,
        metavar="KM",
        help=(
            f"Half-extent of the synthesized fetch square (default %(default)s, "
            f"valid {MIN_RADIUS_KM}-{MAX_RADIUS_KM})."
        ),
    )
    ap.add_argument(
        "--db",
        default=str(db.DEFAULT_DB_PATH),
        metavar="FILE",
        help="SQLite cache database (default %(default)s).",
    )
    args = ap.parse_args()
    db.set_db_path(args.db)

    if not (MIN_RADIUS_KM <= args.radius <= MAX_RADIUS_KM):
        ap.error(
            f"--radius must be between {MIN_RADIUS_KM} and {MAX_RADIUS_KM} km"
        )

    print(f"Geocoding {args.place!r}...")
    anchor = geocode(args.place)
    print(
        f"  anchor: {anchor.name} ({anchor.level}, "
        f"{anchor.osm_type}/{anchor.osm_id}) — fetch r={args.radius}km square"
    )

    layer_ids = args.layers.split(",") if args.layers else None
    print("\nFetching layers...")
    t = time.perf_counter()
    data = fetch(anchor, layer_ids, args.force, args.radius)
    dt = time.perf_counter() - t

    # Use the radius-adjusted anchor in the output so the renderer
    # projects from the bbox the data actually covers.
    fetch_anchor = anchor_at_radius(anchor, args.radius)

    total = sum(len(d["elements"]) for d in data.values())
    nonempty = sum(1 for d in data.values() if d["elements"])
    print(f"\nDone in {dt:.1f}s — {nonempty}/{len(data)} layers populated, {total} elements total")

    output = {
        "anchor": asdict(fetch_anchor),
        "layers": {lid: d["elements"] for lid, d in data.items()},
    }
    Path(args.out).write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    size_kb = Path(args.out).stat().st_size / 1024
    print(f"Wrote {args.out} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
