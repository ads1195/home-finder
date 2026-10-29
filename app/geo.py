"""Geocoding (postcodes.io + Nominatim), routing (OSRM public demo), all cached in SQLite."""
import json, math, re, threading, time
import httpx
from . import db
from .config import NOMINATIM_UA

PC_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.I)
OUT_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\b")
_lock = threading.Lock()
_last = {"nom": 0, "osrm": 0, "pc": 0}
_cli = httpx.Client(timeout=20, headers={"User-Agent": NOMINATIM_UA})

def haversine_km(a_lat, a_lon, b_lat, b_lon):
    R = 6371.0088
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dphi, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))

def _cache_get(key):
    r = db.q1("SELECT value FROM geocache WHERE key=?", (key,))
    return json.loads(r["value"]) if r else "MISS"

def _cache_put(key, val):
    db.x("INSERT OR REPLACE INTO geocache(key,value,created) VALUES(?,?,?)", (key, json.dumps(val), time.time()))

def _throttle(name, gap):
    with _lock:
        w = _last[name] + gap - time.time()
        if w > 0: time.sleep(w)
        _last[name] = time.time()

def geocode_postcode(pc):
    m = PC_RE.search(pc or "")
    if not m: return None
    pc = f"{m.group(1).upper()} {m.group(2).upper()}"
    key = "pc:" + pc
    c = _cache_get(key)
    if c != "MISS": return c
    _throttle("pc", 0.3)
    try:
        r = _cli.get(f"https://api.postcodes.io/postcodes/{pc.replace(' ', '%20')}")
        val = None
        if r.status_code == 200:
            j = r.json()["result"]; val = {"lat": j["latitude"], "lon": j["longitude"], "label": pc}
        elif r.status_code != 404:
            return None   # transient: don't cache
    except Exception:
        return None
    _cache_put(key, val)
    return val

def geocode_text(text):
    """Nominatim free-text search (1 req/s, cached)."""
    key = "nom:" + text.strip().lower()
    c = _cache_get(key)
    if c != "MISS": return c
    _throttle("nom", 1.2)
    val = None
    try:
        r = _cli.get("https://nominatim.openstreetmap.org/search",
                     params={"q": text, "format": "json", "limit": 1, "countrycodes": "gb"})
        if r.status_code == 200:
            j = r.json()
            if j: val = {"lat": float(j[0]["lat"]), "lon": float(j[0]["lon"]), "label": j[0]["display_name"]}
        else:
            return None
    except Exception:
        return None
    _cache_put(key, val)
    return val

def geocode_street(street, city="Leeds"):
    """Structured Nominatim query: averages up to 8 hits for a street (midpoint)."""
    key = f"street:{street.lower()}|{city.lower()}"
    c = _cache_get(key)
    if c != "MISS": return c
    _throttle("nom", 1.2)
    val = None
    try:
        r = _cli.get("https://nominatim.openstreetmap.org/search",
                     params={"street": street, "city": city, "format": "json", "limit": 8, "countrycodes": "gb"})
        if r.status_code != 200: return None
        j = r.json()
        if j:
            val = {"lat": sum(float(x["lat"]) for x in j) / len(j),
                   "lon": sum(float(x["lon"]) for x in j) / len(j), "label": j[0]["display_name"]}
    except Exception:
        return None
    _cache_put(key, val)
    return val

def geocode_any(addr):
    pc = geocode_postcode(addr) if PC_RE.search(addr or "") else None
    if pc and not re.search(r"\b(lane|road|rise|close|street|drive|avenue)\b", addr or "", re.I):
        return pc
    parts = [p.strip() for p in (addr or "").split(",")]
    g = geocode_text(addr)
    if g: return g
    if parts:
        g = geocode_street(parts[0])
        if g: return g
    return pc

def route(lat1, lon1, lat2, lon2):
    """Driving time listing->anchor via OSRM public demo; falls back to an estimate."""
    key = f"osrm:{lat1:.4f},{lon1:.4f}>{lat2:.4f},{lon2:.4f}"
    c = _cache_get(key)
    if c != "MISS": return c
    _throttle("osrm", 0.6)
    straight = haversine_km(lat1, lon1, lat2, lon2)
    try:
        r = _cli.get(f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}",
                     params={"overview": "false"})
        if r.status_code == 200 and r.json().get("code") == "Ok":
            rt = r.json()["routes"][0]
            val = {"drive_min": rt["duration"] / 60.0, "drive_km": rt["distance"] / 1000.0, "method": "osrm"}
            _cache_put(key, val)
            return val
    except Exception:
        pass
    # estimate (not cached so it is retried next cycle)
    km = straight * 1.35
    return {"drive_min": km / 42.0 * 60.0 + 2, "drive_km": km, "method": "estimate"}
