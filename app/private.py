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


GENERIC_IDEAL = {   # benchmark "ideal home": target features. Numbers can be overridden by the config JSON key "ideal_home".
    "label": "Ideal home", "bedrooms": 4, "bathrooms": 2, "detached": True, "sqft": 1430, "m2": 132.7,
    "size_floor_sqft": 1250,      # below this the size score drops faster
    "size_beats_sqft": 1600,      # at/above this a listing counts as bigger than the ideal
    "features": {"open_plan_kitchen_family": True, "bifolds_or_extension": True, "separate_lounge": True, "utility": True,
                 "downstairs_wc": True, "ensuite": True, "family_bathroom": True, "garage_or_drive": True, "rear_garden": True},
    "open_plan_m2": 44.0, "lounge_m2": 16.0, "bedroom_m2": [11.0, 9.4, 8.5, 5.4],
    "rooms": [], "sim_range": [0.55, 0.85], "embeddings": None,
}

def ideal_home():
    import copy
    return deep_merge(copy.deepcopy(GENERIC_IDEAL), load().get("ideal_home"))

_ideal_emb = None
def ideal_embeddings():
    """precomputed CLIP vectors of the ideal home's photos (vectors only; no images) -> list of np.float32 arrays"""
    global _ideal_emb
    if _ideal_emb is not None: return _ideal_emb
    out = []
    try:
        e = ideal_home().get("embeddings")
        if e and e.get("b64"):
            import base64, numpy as np
            a = np.frombuffer(base64.b64decode(e["b64"]), dtype=np.float16).astype(np.float32).reshape(-1, int(e.get("dim", 512)))
            out = [r / (np.linalg.norm(r) or 1) for r in a]
    except Exception as ex:
        print(f"[private] ideal embeddings unreadable ({type(ex).__name__})", flush=True)
    _ideal_emb = out
    return out
