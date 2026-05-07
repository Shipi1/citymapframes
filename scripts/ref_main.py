import json
import random
import time

import requests

from ref_bbox import get_bbox

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
TIMEOUT = 300
CACHE_FILE = "cache.json"
CACHE_TTL = 86400  # 24 hours

# Beaches nwr["natural"="beach"];
# Waterways way["waterway"~"river|stream|canal"]; nwr["natural"="water"];
# Roads way["highway"~"motorway|trunk|primary|secondary"];
# Parks nwr["leisure"~"park|garden"];
# Buildings way["building"];
# Piers nwr["man_made"="pier"];
# Footways way["highway"~"footway"]["crossing"!="traffic_signals"]["footway"!="sidewalk"];
LAYERS = [
    'way["highway"~"motorway|trunk|primary|secondary"];',
    'way["highway"~"tertiary|residential|unclassified|living_street|pedestrian|track"];',
    'nwr["natural"="beach"];',
    'nwr["natural"="water"];',
    'way["waterway"~"river|stream|canal"];',
    'way["natural"="coastline"];',
    'nwr["man_made"="pier"];',
    'nwr["leisure"~"park|garden|stadium|track|pitch|sports_centre"];',
    'nwr["landuse"="grass"];',
    'nwr["landuse"="forest"];',
    'nwr["natural"="scrub"];',
    'way["highway"~"footway"]["crossing"!="traffic_signals"]["footway"!="sidewalk"];',
    'way["building"];',
    'nwr["amenity"~"parking"];',
    'nwr["amenity"~"theatre"];nwr["building"~"roof"];',
]

# LAYERS = ['nwr["landuse"="forest"];','nwr["natural"="scrub"];',]


def query(overpass_ql):
    params = {"data": overpass_ql}
    resp = post(OVERPASS_URL, data=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError, json.JSONDecodeError:
        return {}


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def cache_get(cache, key):
    if key not in cache:
        return None
    entry = cache[key]
    age = time.time() - entry["timestamp"]
    if age > CACHE_TTL:
        print(f"  Cache expired ({age / 3600:.1f}h old)")
        return None
    return entry["data"]


def cache_set(cache, key, data):
    cache[key] = {"timestamp": time.time(), "data": data}


def fetch_layer(layer, bbox, cache):
    q = f"""
    [out:json][timeout:300]{bbox};
    {layer}
    out geom;
        """
    cache_key = f"{layer}{bbox}"
    cached = cache_get(cache, cache_key)
    if cached:
        print(f"Cached {layer} — {len(cached['elements'])} elements")
        return cached

    print(f"Fetching {layer}...")
    data = query(q)
    print(f"  OK — {len(data['elements'])} elements")
    cache_set(cache, cache_key, data)
    save_cache(cache)
    return data


def search(lat, lon, radius, aspect=1, force=False):
    s, w, n, e = get_bbox(lat, lon, radius_km=radius, aspect=aspect)
    bbox = f"[bbox:{s},{w},{n},{e}]"
    cache = {} if force else load_cache()
    responses = []
    for layer in LAYERS:
        responses.append(fetch_layer(layer, bbox, cache))
    with open("nodes.json", "w", encoding="utf-8") as f:
        json.dump(responses, f, ensure_ascii=False)
    print(f"Wrote nodes.json ({sum(len(r['elements']) for r in responses)} elements)")


def retry(fn, max_retries=5, retryable=(504, 429)):
    def wrapper(*args, **kwargs):
        for attempt in range(max_retries + 1):
            resp = fn(*args, **kwargs)
            if resp.status_code in retryable:
                if attempt == max_retries:
                    resp.raise_for_status()
                delay = (2**attempt) * (0.7 + random.random() * 0.6)
                print(f"  {resp.status_code} - retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
            return resp

    return wrapper


post = retry(requests.post)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch OSM data from Overpass API")
    parser.add_argument("--lat", type=float, default=-33.0245)
    parser.add_argument("--lon", type=float, default=-71.5518)
    parser.add_argument("--radius", type=float, default=2)
    parser.add_argument("--aspect", type=float, default=1)
    parser.add_argument(
        "--force", action="store_true", help="Ignore cache, re-fetch all layers"
    )
    args = parser.parse_args()

    try:
        start = time.perf_counter()
        data = search(args.lat, args.lon, args.radius, args.aspect, args.force)
        elapsed = time.perf_counter() - start
        print(f"Done in {elapsed:.1f}s")
    except Exception as e:
        print(e)
