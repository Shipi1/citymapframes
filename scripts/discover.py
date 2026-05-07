"""Tier 2 tag discovery.

Pulls a lightweight sample of OSM tags (no geometry) inside a bbox,
strips tags already covered by main.py's LAYERS and obvious metadata,
and ranks what's left by frequency. Use it to surface city-specific
landmark tags you didn't anticipate.
"""

import argparse
import math
import re
import time
from collections import Counter

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
TIMEOUT = 90


def get_bbox(lat: float, lon: float, radius_km: float = 0.5):
    """Compute (south, west, north, east) bbox for a centered radius."""
    d_lat = radius_km / 111.32
    d_lon = radius_km / (111.32 * math.cos(math.radians(lat)))
    return (
        round(lat - d_lat, 4),
        round(lon - d_lon, 4),
        round(lat + d_lat, 4),
        round(lon + d_lon, 4),
    )

# (key, value_regex | None) — mirrors main.py LAYERS. None = any value under that key is "known".
KNOWN = [
    ("highway", re.compile(
        r"^(motorway|trunk|primary|secondary|tertiary|residential|"
        r"unclassified|living_street|pedestrian|track|footway)$")),
    ("natural",  re.compile(r"^(beach|water|coastline|scrub)$")),
    ("waterway", re.compile(r"^(river|stream|canal)$")),
    ("man_made", re.compile(r"^(pier)$")),
    ("leisure",  re.compile(r"^(park|garden|stadium|track|pitch|sports_centre)$")),
    ("landuse",  re.compile(r"^(grass|forest)$")),
    ("amenity",  re.compile(r"^(parking|theatre)$")),
    ("building", None),
]

# Metadata tags that clutter results without describing geometry.
BORING_EXACT = {
    "name", "ref", "note", "fixme", "source", "operator", "brand",
    "website", "phone", "email", "opening_hours", "start_date",
    "old_name", "alt_name", "loc_name", "official_name", "short_name",
    "description", "wikipedia", "wikidata", "layer", "level", "height",
    "ele", "maxspeed", "lanes", "oneway", "surface", "access",
    "bicycle", "foot", "motor_vehicle", "horse", "smoothness",
    "lit", "covered", "wheelchair", "capacity", "fee",
}
BORING_PREFIXES = (
    "addr:", "name:", "contact:", "tiger:", "gnis:", "nhd:",
    "is_in", "seamark:", "created_by", "building:levels",
)


def is_known(key, value):
    for k, pat in KNOWN:
        if key == k and (pat is None or pat.match(value)):
            return True
    return False


def is_boring(key):
    if key in BORING_EXACT:
        return True
    return any(key.startswith(p) for p in BORING_PREFIXES)


def fetch_sample(bbox_str, limit):
    q = f"""
    [out:json][timeout:{TIMEOUT}]{bbox_str};
    nwr;
    out tags {limit};
    """
    resp = requests.post(OVERPASS_URL, data={"data": q}, timeout=TIMEOUT + 10)
    resp.raise_for_status()
    return resp.json()


def aggregate(elements):
    pair_counts = Counter()   # (key, value)
    key_counts = Counter()    # key only
    for el in elements:
        for k, v in (el.get("tags") or {}).items():
            if is_boring(k):
                continue
            pair_counts[(k, v)] += 1
            key_counts[k] += 1
    return pair_counts, key_counts


def rank(pair_counts, min_count):
    out = []
    for (k, v), c in pair_counts.most_common():
        if is_known(k, v):
            continue
        if c < min_count:
            continue
        out.append((k, v, c))
    return out


def main():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="discover.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Tier-2 tag discovery: find OSM tags in a bbox that are NOT already\n"
            "covered by layers.json. Useful for finding city-specific landmarks\n"
            "(funiculars, ferries, place=quarter labels, etc.) you didn't think\n"
            "to add to the layer registry."
        ),
        epilog=(
            "Examples:\n"
            "  discover.py                                # default coords (Valparaíso)\n"
            "  discover.py --lat 38.72 --lon -9.14 --radius 3   # Lisbon\n"
            "  discover.py --top 50 --min-count 5\n"
            "\n"
            "Workflow: run this in a city you care about, scan the candidate list,\n"
            "promote interesting tags by adding them to layers.json.\n"
            "Note: this script still uses lat/lon (predates geocode.py)."
        ),
    )
    ap.add_argument("--lat", type=float, default=-33.0245,
                    metavar="DEG", help="Latitude of bbox center (default Valparaíso).")
    ap.add_argument("--lon", type=float, default=-71.5518,
                    metavar="DEG", help="Longitude of bbox center (default Valparaíso).")
    ap.add_argument("--radius", type=float, default=2.0,
                    metavar="KM", help="Half-extent of the discovery bbox (default %(default)s km).")
    ap.add_argument("--limit", type=int, default=5000, metavar="N",
                    help="Overpass element cap — `out tags N` (default %(default)s).")
    ap.add_argument("--top", type=int, default=30, metavar="N",
                    help="Show top N candidates (default %(default)s).")
    ap.add_argument("--min-count", type=int, default=3, metavar="N",
                    help="Suppress candidates seen fewer than N times (default %(default)s).")
    args = ap.parse_args()

    s, w, n, e = get_bbox(args.lat, args.lon, radius_km=args.radius)
    bbox_str = f"[bbox:{s},{w},{n},{e}]"
    print(f"Discovering tags in {bbox_str}")
    print(f"  sample cap: {args.limit} elements, min count: {args.min_count}")

    t = time.perf_counter()
    data = fetch_sample(bbox_str, args.limit)
    dt = time.perf_counter() - t
    els = data.get("elements", [])
    print(f"  fetched {len(els)} elements in {dt:.1f}s\n")

    pair_counts, key_counts = aggregate(els)
    ranked = rank(pair_counts, args.min_count)

    print(f"Top keys by instance count (informational):")
    for k, c in key_counts.most_common(10):
        print(f"  {k:<20} {c}")

    print(f"\nTop {args.top} candidate tags NOT covered by your layers:")
    if not ranked:
        print("  (nothing above threshold)")
        return
    width = max(len(f"{k}={v}") for k, v, _ in ranked[: args.top])
    for k, v, c in ranked[: args.top]:
        print(f"  {f'{k}={v}':<{width}}  {c}")


if __name__ == "__main__":
    main()
