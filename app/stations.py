"""Nearest rail station (info + tiny score bonus only).  Small built-in table plus optional extra stations
from the private config (PROPDASH_CONFIG_JSON "stations").  Connectivity text is INDICATIVE."""
from .geo import haversine_km
from . import private
BASE = [("Leeds", 53.7954, -1.5476, "Hub: direct to York, Manchester (TransPennine), London (LNER), many more.")]
def _all(): return BASE + private.extra_stations()
def nearest(lat, lon):
    best = None
    for n, la, lo, info in _all():
        d = haversine_km(lat, lon, la, lo)
        if best is None or d < best["km"]:
            best = {"name": n, "km": round(d, 2), "info": info}
    return best
