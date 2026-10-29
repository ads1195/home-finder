"""Areas to avoid.  The list itself is PRIVATE: it comes from the config JSON (criteria.avoid_areas) - nothing is hard-coded here.
Each entry: {"name": "Area", "match": [regex, ...],  # tested (case-insensitive) against the listing's address + title only
             "sectors": ["LS14 1", ...],              # full postcodes whose sector (outcode + first digit) is in this list
             "circles": [[lat, lon, radius_m], ...]}  # listing coordinates inside any circle
-> check() returns the area's name (or None)."""
import re, math

_cmp = {}
_PC = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d)[A-Z]{2}\b")

def _rx(a):
    k = a.get("name")
    if k not in _cmp:
        _cmp[k] = [re.compile(x, re.I) for x in a.get("match") or []]
    return _cmp[k]

def _hav_m(la1, lo1, la2, lo2):
    p = math.pi / 180
    h = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(h))

def check(l, areas):
    if not areas: return None
    text = re.sub(r"[^a-z0-9' ]+", " ", ((l.get("address") or "") + " " + (l.get("title") or "")).lower())
    pc = (l.get("postcode") or "").upper(); m = _PC.search(pc) or _PC.search((l.get("address") or "").upper())
    sector = f"{m.group(1)} {m.group(2)}" if m else None
    lat, lon = l.get("lat"), l.get("lon")
    for a in areas:
        for rx in _rx(a):
            if rx.search(text): return a["name"]
        if sector and sector in (a.get("sectors") or []): return a["name"]
        if lat is not None and lon is not None:
            for c in a.get("circles") or []:
                if _hav_m(lat, lon, c[0], c[1]) <= c[2]: return a["name"]
    return None
