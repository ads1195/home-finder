"""Street-level crime context from the free police.uk API (https://data.police.uk).
One lookup = the latest few months of recorded crime inside a ~500 m box around a point, averaged per month.
Results are cached per rounded lat/lng (3 d.p. ~ 100 m) in the persistent analysis cache, so each place is fetched once.
Lookups are made one at a time and slowly (well under the API's 15 requests/second limit)."""
import time, math, re, statistics, threading
import httpx
from . import cache

MONTHS = 4                 # latest N published months, averaged
HALF_M = 500.0             # half-side of the search box
GAP = 0.4                  # seconds between API calls
TTL = 60 * 86400           # re-fetch a place after this long
BASE_N = 15                # need this many places before the median of our own listings is trusted
FALLBACK_MEDIAN = 30.0     # fixed Leeds-suburban benchmark for a ~1 km box (index points / month) used until then
API = "https://data.police.uk/api"
_lock = threading.Lock()
_last = [0.0]
_months = {"t": 0, "m": []}
stats = {"fetched": 0, "cached": 0, "failed": 0, "calls": 0}

def key(lat, lon): return f"{lat:.3f},{lon:.3f}"
def _store(): return cache.D.setdefault("crime", {})

def _get(path, params=None):
    for attempt in range(3):
        wait = GAP - (time.time() - _last[0])
        if wait > 0: time.sleep(wait)
        _last[0] = time.time(); stats["calls"] += 1
        try:
            r = httpx.get(API + path, params=params, timeout=40, headers={"User-Agent": "home-finder/1.0"})
        except Exception:
            time.sleep(3 * (attempt + 1)); continue
        if r.status_code == 200: return r.json()
        if r.status_code in (429, 502, 503): time.sleep(5 * (attempt + 1)); continue
        return None
    return None

def latest_months():
    if _months["m"] and time.time() - _months["t"] < 86400: return _months["m"]
    d = _get("/crime-last-updated")
    if not d or not d.get("date"): return _months["m"]
    y, m = int(d["date"][:4]), int(d["date"][5:7])
    ms = []
    for _ in range(MONTHS):
        ms.append(f"{y:04d}-{m:02d}"); m -= 1
        if m == 0: y, m = y - 1, 12
    _months.update(t=time.time(), m=ms)
    return ms

def _poly(lat, lon):
    dl = HALF_M / 111320.0; dn = HALF_M / (111320.0 * math.cos(math.radians(lat)))
    pts = [(lat - dl, lon - dn), (lat - dl, lon + dn), (lat + dl, lon + dn), (lat + dl, lon - dn)]
    return ":".join(f"{a:.5f},{b:.5f}" for a, b in pts)

_nb = {}
def _link(lat, lon):
    """police.uk neighbourhood page for this point (2 small calls, remembered per neighbourhood), or None"""
    try:
        d = _get("/locate-neighbourhood", {"q": f"{lat:.5f},{lon:.5f}"})
        if not d or not d.get("force"): return None
        k = (d["force"], d["neighbourhood"])
        if k not in _nb:
            n = _get(f"/{d['force']}/{d['neighbourhood']}")
            _nb[k] = ("/".join([d["force"] + "-police", re.sub(r"[^a-z0-9]+", "-", (n or {}).get("name", "").lower()).strip("-")]) if n and n.get("name") else None)
        return _nb[k]
    except Exception:
        return None

def fetch(lat, lon):
    """-> {"n": crimes/month (excl. anti-social behaviour), "v": violent/month, "b": burglary/month, "a": ASB/month, "t":, "m": months} or None"""
    ms = latest_months()
    if not ms: return None
    tot = vio = bur = asb = 0; got = 0
    for mo in ms:
        d = _get("/crimes-street/all-crime", {"poly": _poly(lat, lon), "date": mo})
        if d is None: continue
        got += 1
        for c in d:
            cat = c.get("category")
            if cat == "anti-social-behaviour": asb += 1; continue
            tot += 1
            if cat in ("violent-crime", "robbery"): vio += 1
            elif cat == "burglary": bur += 1
    if not got: return None
    return {"u": _link(lat, lon), "n": round(tot / got, 1), "v": round(vio / got, 1), "b": round(bur / got, 2), "a": round(asb / got, 1), "t": round(time.time()), "m": got}

def lookup(lat, lon, fetch_missing=True):
    """cached result for this place (fetching it once if allowed)"""
    if lat is None or lon is None: return None
    k = key(lat, lon)
    with _lock:
        e = _store().get(k)
        if e and time.time() - e["t"] < TTL: stats["cached"] += 1; return e
    if not fetch_missing: return e
    r = fetch(round(lat, 3), round(lon, 3))
    if r is None: stats["failed"] += 1; return e
    with _lock: _store()[k] = r
    stats["fetched"] += 1
    cache.touch()
    return r

def index(e):
    """one number per place: crimes/month with violent crime and burglary counted extra"""
    return e["n"] + 0.5 * e["v"] + 1.0 * e["b"]

def baseline():
    """-> (median, p90, n_places): the median across every place we have looked up (or a fixed benchmark while there are few)"""
    vals = sorted(index(e) for e in _store().values() if time.time() - e["t"] < 150 * 86400)
    if len(vals) < BASE_N: return FALLBACK_MEDIAN, FALLBACK_MEDIAN * 1.9, len(vals)
    med = statistics.median(vals); p90 = vals[min(len(vals) - 1, int(len(vals) * 0.9))]
    return med, max(p90, med * 1.5), len(vals)

def rate(e):
    """-> dict for the evaluation: level low/average/high/review, ratio to baseline, score adjustment (points of 100)"""
    if not e: return None
    med, p90, n = baseline(); ix = index(e); ratio = ix / med if med else 1.0
    if ratio <= 0.6: level, adj = "low", 2.0
    elif ratio < 1.8: level, adj = "average", 0.0
    else:
        level = "high"; adj = -min(8.0, 3.0 + (ratio - 1.8) * 3.0)
    if ix >= p90 and ratio >= 1.5: level = "review"
    return {"level": level, "ratio": round(ratio, 2), "adj": round(adj, 1), "n": e["n"], "v": e["v"], "b": e["b"], "a": e["a"],
            "months": e["m"], "url": ("https://www.police.uk/pu/your-area/" + e["u"] + "/?tab=crimemap") if e.get("u") else None, "baseline": round(med, 1), "base_places": n}
