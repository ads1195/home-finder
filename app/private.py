"""Personal details (home anchors, current home, local search area) are NOT stored in code.
They come from, in order:
  1. env PROPDASH_CONFIG_JSON   (a JSON object; on Hugging Face this is a Space secret)
  2. file PROPDASH_CONFIG_FILE, or <data dir>/private-config.json  (local use; never committed)
Shape (all keys optional):
  {"criteria": {partial overrides deep-merged into the default criteria: anchors, search_area, search_locations ...},
   "current_home": {merged over the generic placeholder baseline home},
   "sources": [extra/override source entries, matched by id, appended to the portal defaults],
   "stations": [[name, lat, lon, info], ...]   (nearest-station table; Leeds hub is built in),
   "zoopla_probe_location": "town name"}
A restored database snapshot already contains these settings, so this only matters for a fresh start."""
import json, os
from pathlib import Path

GENERIC_CRITERIA = {
    "search_area": {"name": "Leeds (generic placeholder)", "lat": 53.8008, "lon": -1.5491, "radius_miles": 9.0},
    "search_locations": ["Leeds"],
    "anchors": [{"name": "Anchor 1 (set in Settings)", "address": "Leeds city centre", "lat": None, "lon": None,
                 "max_drive_min": 25, "max_straight_km": 20, "weight": 1.0}],
}
GENERIC_HOME = {
    "confirmed": False, "address": "Your current home (set in Reference homes)",
    "bedrooms": 3, "bathrooms": 1, "ensuites": 0, "receptions": 1, "downstairs_wc": False, "garage": False, "driveway": True,
    "utility": False, "study": False, "sqft": 915, "m2": 85,
    "floor_areas_sqft": {}, "rooms": [], "floorplan": None, "photos": [], "source_url": None, "sales": [],
    "description": "Placeholder baseline - edit under Reference homes.",
}
_cache = None

def load():
    global _cache
    if _cache is not None: return _cache
    raw = os.environ.get("PROPDASH_CONFIG_JSON", "").strip()
    data = {}
    try:
        if raw:
            data = json.loads(raw)
        else:
            base = Path(os.environ.get("PROPDASH_DATA") or Path(__file__).resolve().parent.parent / "data")
            f = Path(os.environ.get("PROPDASH_CONFIG_FILE") or base / "private-config.json")
            if f.exists(): data = json.loads(f.read_text())
    except Exception as e:   # never echo the content (may be personal / secret)
        print(f"[private] could not parse personal config ({type(e).__name__}); using generic defaults", flush=True)
        data = {}
    _cache = data if isinstance(data, dict) else {}
    return _cache

def deep_merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict): deep_merge(base[k], v)
        else: base[k] = v
    return base

def finalize_config(cfg):
    """generic placeholders first, then the personal overrides"""
    import copy
    crit = cfg["criteria"]
    for k, v in GENERIC_CRITERIA.items(): crit.setdefault(k, copy.deepcopy(v))
    deep_merge(crit, load().get("criteria"))
    have = {x.get("id"): i for i, x in enumerate(cfg.get("sources", []))}
    for x in load().get("sources") or []:
        if not isinstance(x, dict) or not x.get("id"): continue
        if x["id"] in have: cfg["sources"][have[x["id"]]].update(x)
        else: cfg["sources"].append(copy.deepcopy(x))
    return cfg

def extra_stations():
    out = []
    for r in load().get("stations") or []:
        try: out.append((str(r[0]), float(r[1]), float(r[2]), str(r[3]) if len(r) > 3 else ""))
        except Exception: pass
    return out

def current_home_default():
    import copy
    return deep_merge(copy.deepcopy(GENERIC_HOME), load().get("current_home"))
