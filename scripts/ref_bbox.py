import math


def get_bbox(lat, lon, radius_km=0.5, aspect=1):
    dLat = (radius_km / 111.32) * aspect
    dLon = radius_km / (111.32 * math.cos(math.radians(lat)))
    s = round(lat - dLat, 4)
    n = round(lat + dLat, 4)
    w = round(lon - dLon, 4)
    e = round(lon + dLon, 4)
    return s, w, n, e


def get_formatted_bbox(lat, lon, radius_km=0.5, aspect=1.35):
    ### returns a string of format [bbox:{s},{w},{n},{e}]
    s, w, n, e = get_bbox(lat, lon, radius_km=radius_km)
    return f"[bbox:{s},{w},{n},{e}]"


print(get_formatted_bbox(-33.0245, -71.5518, 2))
