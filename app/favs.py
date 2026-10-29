"""Saved homes: in-memory server copy (browser localStorage is the master) + a modest ranking boost for
listings similar to what was saved. The boost only applies to listings that already pass every hard filter,
is capped at MAX_BOOST points and never changes the price band."""
import json, threading
import numpy as np
from . import db
from .config import DATA

MAX_BOOST = 4.0        # points (score is 0-100)
MAX_ITEMS = 200
_lock = threading.Lock()
_items = {}            # url -> entry {url, saved, updated, note, snap, feat}; rebuilt from the browser on every sync
_ver = 0
_cache = {"key": None, "boosts": {}}
_emb_cache = {}        # listing id -> (mtime, mean unit vector)

def norm_url(u): return (u or "").split("#")[0].strip()

def merge(client_items):
    """last-write-wins per url (entries carry `updated`). Returns the merged map."""
    global _ver
    with _lock:
        for u, it in (client_items or {}).items():
            if not isinstance(it, dict): continue
            u = norm_url(u)
            if "://" not in u: continue
            old = _items.get(u)
            if old is None or (it.get("updated") or 0) >= (old.get("updated") or 0):
                e = {k: it.get(k) for k in ("saved", "updated", "note", "snap", "feat")}
                e["url"] = u; e["note"] = (e.get("note") or "")[:600]
                _items[u] = e
        if len(_items) > MAX_ITEMS:
            keep = sorted(_items.items(), key=lambda kv: -(kv[1].get("updated") or 0))[:MAX_ITEMS]
            _items.clear(); _items.update(keep)
        _ver += 1
        return dict(_items)

def saved_urls():
    return [u for u, v in _items.items() if v.get("saved")]

def features(l, ev):
    """small JSON-able description of a listing, stored with each saved home and used for similarity"""
    if not ev: return None
    drv = [v["drive_min"] for v in (ev.get("drive") or {}).values() if v.get("drive_min") is not None]
    ck = {k: v["state"] for k, v in (ev.get("checklist") or {}).items()}
    return {"price": l.get("price"), "beds": l.get("bedrooms"), "type": (l.get("property_type") or "").lower(),
            "ck": ck, "drive": round(sum(drv) / len(drv), 1) if drv else None,
            "sqft": ((ev.get("vs_current") or {}).get("sqft") or [None])[0]}

def share_info(ev):
    """ideal-home fit % and a few key wins, for the Share button (no personal data)"""
    vi = (ev or {}).get("vs_ideal")
    if not vi: return None
    ok = {r["key"] for r in vi["rows"] if r["status"] in ("meets", "beats")}
    names = [("lounge", "separate lounge"), ("kitchen_family", "open-plan family kitchen"), ("utility", "utility"), ("garden", "garden"), ("parking", "front parking")]
    wins = [n for k, n in names if k in ok]
    if "wc" in ok and "ensuite" in ok: wins.append("WC + en-suite")
    elif "wc" in ok: wins.append("downstairs WC")
    elif "ensuite" in ok: wins.append("en-suite")
    return {"fit": round(vi["fit"] * 100), "wins": wins}

def feat_sim(a, b):
    if not a or not b: return None
    parts = []
    if a.get("price") and b.get("price"): parts.append((max(0, 1 - abs(a["price"] - b["price"]) / 100000), 0.20))
    if a.get("beds") and b.get("beds"): parts.append((1.0 if a["beds"] == b["beds"] else 0.5 if abs(a["beds"] - b["beds"]) == 1 else 0.0, 0.15))
    if a.get("type") and b.get("type"): parts.append((1.0 if a["type"] == b["type"] else 0.3, 0.15))
    ca = {k for k, s in (a.get("ck") or {}).items() if s == "confirmed"}
    cb = {k for k, s in (b.get("ck") or {}).items() if s == "confirmed"}
    if ca or cb: parts.append((len(ca & cb) / len(ca | cb), 0.30))
    if a.get("drive") is not None and b.get("drive") is not None: parts.append((max(0, 1 - abs(a["drive"] - b["drive"]) / 20), 0.10))
    if a.get("sqft") and b.get("sqft"): parts.append((max(0, 1 - abs(a["sqft"] - b["sqft"]) / b["sqft"]), 0.10))
    w = sum(p[1] for p in parts)
    return sum(p[0] * p[1] for p in parts) / w if w else None

def _mean_emb(lid):
    f = DATA / "images" / str(lid) / "emb.npy"
    try: mt = f.stat().st_mtime
    except Exception: return None
    c = _emb_cache.get(lid)
    if c and c[0] == mt: return c[1]
    try:
        a = np.load(f); v = a.mean(axis=0); n = np.linalg.norm(v); v = v / n if n else v
    except Exception: return None
    _emb_cache[lid] = (mt, v)
    return v

def boosts():
    """{listing_id: points}. Only active listings that pass all hard filters; never the saved homes themselves."""
    prof = [(u, v) for u, v in _items.items() if v.get("saved")]
    if not prof: return {}
    st = db.q1("SELECT COUNT(*) n, MAX(updated) m FROM evaluation")
    key = (_ver, st["n"], st["m"], len(prof))
    if _cache["key"] == key: return _cache["boosts"]
    sav_emb = []
    for u, v in prof:
        r = db.q1("SELECT listing_id FROM sightings WHERE url=?", (u,))
        e = _mean_emb(r["listing_id"]) if r else None
        if e is not None: sav_emb.append(e)
    saved_set = {u for u, _ in prof}
    out, raw = {}, {}
    rows = db.q("""SELECT l.id, l.price, l.bedrooms, l.property_type, e.data FROM listings l JOIN evaluation e ON e.listing_id=l.id
                   WHERE l.active=1 AND l.hidden=0 AND e.passes=1""")
    for r in rows:
        if any(s["url"] in saved_set for s in db.q("SELECT url FROM sightings WHERE listing_id=?", (r["id"],))): continue
        ev = json.loads(r["data"]); f = features(r, ev)
        sims = [x for x in (feat_sim(f, v.get("feat")) for _, v in prof) if x is not None]
        if not sims: continue
        s = max(sims)
        me = _mean_emb(r["id"])
        if me is not None and sav_emb:
            pc = max(float(np.dot(me, e)) for e in sav_emb)
            s = 0.5 * s + 0.5 * min(1, max(0, (pc - 0.55) / 0.30))
        raw[r["id"]] = s
    # relative scale: only listings that resemble the saved homes MORE than the typical passing listing get a nudge
    if raw:
        v = sorted(raw.values()); lo, hi = v[len(v) // 2], v[int(len(v) * 0.95)]
        for lid, s in raw.items():
            b = round(MAX_BOOST * min(1, max(0, (s - lo) / (hi - lo))), 1) if hi > lo else 0
            if b >= 0.5: out[lid] = b
    _cache["key"], _cache["boosts"] = key, out
    return out
