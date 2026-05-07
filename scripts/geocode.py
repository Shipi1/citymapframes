"""Resolve a place query to a stable parent anchor.

Input: free-text query like "Cerro Alegre, Valparaíso".
Output: an Anchor pointing at the smallest *parent* level that's recognized
        as a fetch-sized place (city/town/municipality/village by default).

The anchor's osm_id becomes the cache key — so panning around inside the
parent never fragments the cache, regardless of what sub-place the user
typed to get here.

Respects Nominatim usage policy: 1 req/sec, descriptive User-Agent,
results cached to disk indefinitely (place boundaries don't change).
"""

import json
import math
import time
from dataclasses import asdict, dataclass

import requests

import db

NOMINATIM_URL = "https://nominatim.openstreetmap.org"
USER_AGENT = "CityMapFrames/0.1 (personal map renderer; contact via github)"
RATE_LIMIT_SEC = 1.0
DEFAULT_MAX_EXTENT_KM = 50.0       # admin bbox wider than this → synthesize
DEFAULT_FALLBACK_RADIUS_KM = 8.0   # synthesized half-extent around centroid

# Anchor candidates, highest priority first. We pick the FIRST one
# present in the address hierarchy and stick with it (synthesizing the
# fetch bbox if its admin boundary is geographically scattered).
ANCHOR_LEVELS = [
    "city", "town", "municipality", "village",
    "county", "state",
    # Fallbacks only if none of the above are present:
    "suburb", "city_district", "neighbourhood",
]


@dataclass
class Anchor:
    osm_id: int
    osm_type: str            # "relation" | "way" | "node"
    name: str                # short name
    display_name: str        # full Nominatim display
    level: str               # which hierarchy level we picked
    lat: float               # Nominatim's centroid (usually urban core)
    lon: float
    bbox: tuple              # (south, west, north, east) — the FETCH bbox
    extent_km: float         # max of bbox width/height
    bbox_synthesized: bool   # True if we replaced the admin bbox


# ---------- rate limit ----------

_last_request = 0.0


def _rate_limit():
    global _last_request
    wait = RATE_LIMIT_SEC - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


# ---------- Nominatim ----------

def _search(query: str) -> list[dict]:
    """Cached Nominatim search. Cache lives in db.geocode_cache."""
    hit = db.get_geocode(query)
    if hit is not None:
        return hit
    _rate_limit()
    r = requests.get(
        f"{NOMINATIM_URL}/search",
        params={"q": query, "format": "json", "addressdetails": 1, "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    r.raise_for_status()
    results = r.json()
    db.put_geocode(query, results)
    return results


# ---------- geometry ----------

def _bbox_extent_km(south, west, north, east) -> float:
    """Max of bbox width and height in km — robust to scattered admin polygons."""
    mean_lat = (south + north) / 2
    height_km = abs(north - south) * 111.32
    width_km = abs(east - west) * 111.32 * math.cos(math.radians(mean_lat))
    return max(height_km, width_km)


def _synthesize_bbox(lat: float, lon: float, radius_km: float):
    """Build a tight bbox around (lat, lon) when the admin polygon is unusable."""
    d_lat = radius_km / 111.32
    d_lon = radius_km / (111.32 * math.cos(math.radians(lat)))
    return (lat - d_lat, lon - d_lon, lat + d_lat, lon + d_lon)


def _result_to_anchor(
    result: dict,
    level: str,
    max_extent_km: float,
    fallback_radius_km: float,
) -> Anchor:
    # Nominatim returns boundingbox as [south, north, west, east] of strings.
    bb = result["boundingbox"]
    south, north = float(bb[0]), float(bb[1])
    west, east = float(bb[2]), float(bb[3])
    lat, lon = float(result["lat"]), float(result["lon"])
    short = (
        result.get("name")
        or result.get("display_name", "").split(",")[0].strip()
    )

    extent = _bbox_extent_km(south, west, north, east)
    synthesized = False
    if extent > max_extent_km:
        # Admin bbox is geographically scattered (islands, weird jurisdictions,
        # or the place is just too big to fetch in one Overpass call).
        # Anchor identity stays the same osm_id; only the fetch bbox shrinks.
        south, west, north, east = _synthesize_bbox(lat, lon, fallback_radius_km)
        extent = _bbox_extent_km(south, west, north, east)
        synthesized = True

    return Anchor(
        osm_id=int(result["osm_id"]),
        osm_type=result["osm_type"],
        name=short,
        display_name=result["display_name"],
        level=level,
        lat=lat,
        lon=lon,
        bbox=(south, west, north, east),
        extent_km=extent,
        bbox_synthesized=synthesized,
    )


# ---------- main entry ----------

def geocode(
    query: str,
    max_extent_km: float = DEFAULT_MAX_EXTENT_KM,
    fallback_radius_km: float = DEFAULT_FALLBACK_RADIUS_KM,
) -> Anchor:
    """Resolve `query` to a parent anchor.

    Algorithm:
      1. Geocode the full query. Read its address hierarchy.
      2. Walk ANCHOR_LEVELS in priority order. Pick the FIRST level
         present in the hierarchy and anchor there — we don't fall through
         to smaller levels just because a city's admin bbox is unusual.
      3. If the chosen anchor's bbox extent exceeds max_extent_km
         (e.g., a commune that includes offshore islands), keep the same
         osm_id but synthesize a tight fetch bbox around its centroid.
    """
    results = _search(query)
    if not results:
        raise ValueError(f"No geocoding result for: {query!r}")
    primary = results[0]
    address = primary.get("address", {})
    country = address.get("country", "")
    primary_name = (
        primary.get("name")
        or primary.get("display_name", "").split(",")[0].strip()
    )

    for level in ANCHOR_LEVELS:
        if level not in address:
            continue
        place_name = address[level]
        if place_name == primary_name:
            # Primary result is already at this level — no second call needed.
            anchor = _result_to_anchor(primary, level, max_extent_km, fallback_radius_km)
            db.put_anchor(anchor)
            return anchor
        sub_query = f"{place_name}, {country}" if country else place_name
        sub = _search(sub_query)
        if sub:
            anchor = _result_to_anchor(sub[0], level, max_extent_km, fallback_radius_km)
            db.put_anchor(anchor)
            return anchor
        # Couldn't resolve this level via secondary search — try the next one.

    # No hierarchy levels we recognize — fall back to the primary result itself.
    anchor = _result_to_anchor(primary, "raw", max_extent_km, fallback_radius_km)
    db.put_anchor(anchor)
    return anchor


# ---------- CLI ----------

if __name__ == "__main__":
    import argparse
    import sys

    # Windows console defaults to cp1252; force UTF-8 so non-ASCII place names
    # round-trip cleanly through stdout.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        prog="geocode.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Resolve a place name to a stable parent anchor (osm_id + bbox).\n"
            "Sub-place inputs climb up to their city/town parent so all queries\n"
            "in the same place share one cache cell."
        ),
        epilog=(
            "Examples:\n"
            "  geocode.py \"Lisbon\"\n"
            "  geocode.py \"Cerro Alegre, Valparaíso\"\n"
            "  geocode.py \"Tokyo\" --radius 5\n"
            "  geocode.py \"Reykjavík\" --max-extent 100\n"
            "\n"
            "Output: a JSON Anchor with osm_id, osm_type, bbox, centroid,\n"
            "extent_km, and bbox_synthesized flag.\n"
            "Results cached to ./geocode_cache.json (delete to refresh).\n"
            "See MANUAL.md for full reference."
        ),
    )
    ap.add_argument("query", help="Place name. Quote it if it has spaces or commas.")
    ap.add_argument(
        "--max-extent",
        type=float,
        default=DEFAULT_MAX_EXTENT_KM,
        metavar="KM",
        help=(
            "If the admin polygon's longest side exceeds this, synthesize a tighter "
            f"fetch bbox around the centroid (default %(default)s km). Raise this if "
            "you have a real city whose admin polygon is fine but slightly oversized."
        ),
    )
    ap.add_argument(
        "--radius",
        type=float,
        default=DEFAULT_FALLBACK_RADIUS_KM,
        metavar="KM",
        help=(
            "Half-extent of the synthesized bbox when synthesis kicks in "
            "(default %(default)s km, total box is 2× this wide)."
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

    t = time.perf_counter()
    a = geocode(args.query, args.max_extent, args.radius)
    dt = time.perf_counter() - t
    print(json.dumps(asdict(a), ensure_ascii=False, indent=2))
    flag = " [synthesized]" if a.bbox_synthesized else ""
    print(f"\n[{dt:.2f}s, level={a.level}, extent={a.extent_km:.1f} km{flag}]")
