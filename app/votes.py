"""Downvotes ("Not for us") and simple, transparent learning from them.

* Votes are keyed by listing URL (like saved homes) and kept in memory; the browser's localStorage is a second copy and the
  server copy is persisted in the PRIVATE cache repo (cache.D["votes"]), so they survive restarts.  They never touch the public repo.
* Every reason pill maps to one or more scoring weights.  Weight multiplier = 1 + (CAP-1) * n/(n+K): 1 vote -> +11%, 5 -> +38%,
  never above CAP (2x).  Only votes newer than the last "reset" count.
* 'Area / street' (and 'busy road') add a small penalty to homes within ~300 m of, or on the same street as, a downvoted home;
  'Style / looks' adds a mild penalty by photo similarity (CLIP vectors already stored for analysis; no model is run here).
* Favourites keep their symmetric nudge (favs.py)."""
import json, math, re, threading, time
import numpy as np
from . import db, favs

REASONS = [
    ("openplan", "No open-plan kitchen/dining/living space like our ideal home"),
    ("lounge", "No separate lounge"),
    ("kitchen_small", "Kitchen/family space too small (no room for sofa+TV)"),
    ("layout", "Layout unlike our ideal home"),
    ("utility", "No utility"),
    ("wc", "No downstairs WC / en-suite"),
    ("beds", "Too few bedrooms / too small"),
    ("garden", "Garden too small"),
    ("overlooked", "Overlooked"),
    ("parking", "No front parking / driveway"),
    ("condition", "Needs work / dated"),
    ("area", "Area / street not for us"),
    ("far", "Too far / bad location"),
    ("price", "Price / poor value"),
    ("style", "Style / looks (e.g. new-build estate, exterior)"),
    ("noise", "Busy road / noise"),
    ("other", "Other"),
]
LABEL = dict(REASONS)
TRAIT_SEV = {"openplan", "lounge", "kitchen_small", "utility", "wc", "beds", "garden", "overlooked", "parking", "condition", "layout", "far", "price"}
# reason -> scoring knobs ("ck:" checklist weight, "ideal:" row weight in the ideal-home fit, "w:" score_weights, "adj:" additive adjustment)
KNOBS = {
    "openplan": ["ck:kitchen_family", "ideal:kitchen_family", "w:openplan"],
    "lounge": ["ideal:lounge", "ideal:lounge_area"],
    "kitchen_small": ["ideal:kitchen_area"],
    "layout": ["w:ideal"],
    "utility": ["ck:utility", "ideal:utility"],
    "wc": ["ck:wc_or_ensuite", "ideal:wc", "ideal:ensuite"],
    "beds": ["ideal:bedrooms", "ideal:bed_sizes", "w:size"],
    "garden": ["ck:garden_size", "ideal:garden"],
    "overlooked": ["ck:not_overlooked"],
    "parking": ["ck:parking", "ideal:parking"],
    "condition": ["adj:condition"],
    "area": ["adj:crime"],
    "far": ["w:drive"],
    "price": ["w:price"],
}
KNOB_NAME = {
    "ck:kitchen_family": "Open-plan kitchen/family (checklist)", "ideal:kitchen_family": "Open-plan kitchen/family (ideal fit)",
    "ideal:lounge": "Separate lounge (ideal fit)", "ideal:lounge_area": "Lounge size (ideal fit)", "ideal:kitchen_area": "Kitchen/family size (ideal fit)",
    "w:ideal": "Overall ideal-home fit", "ck:utility": "Utility (checklist)", "ideal:utility": "Utility (ideal fit)",
    "ck:wc_or_ensuite": "WC / en-suite (checklist)", "ideal:wc": "Downstairs WC (ideal fit)", "ideal:ensuite": "En-suite (ideal fit)",
    "ideal:bedrooms": "Bedrooms (ideal fit)", "ideal:bed_sizes": "Bedroom sizes (ideal fit)", "w:size": "Floor-area score",
    "ck:garden_size": "Garden (checklist)", "ideal:garden": "Garden (ideal fit)", "ck:not_overlooked": "Not overlooked (checklist)",
    "ck:parking": "Parking (checklist)", "ideal:parking": "Parking (ideal fit)", "adj:condition": "Condition penalty (max)",
    "adj:crime": "Crime penalty (max)", "w:drive": "Drive-time score", "w:price": "Price-band score", "w:openplan": "Open-plan family space score",
}
IDEAL_W = {"kitchen_family": 4, "kitchen_area": 3, "lounge": 3, "lounge_area": 1.5, "utility": 3, "wc": 2.5, "ensuite": 2.5, "bedrooms": 2,
           "bed_sizes": 2, "parking": 1.5, "garden": 2}
ADJ_MAX = {"condition": 6.0, "crime": 8.0}
CAP, K = 2.5, 5.0                   # weight multiplier: 1 vote x1.25, 2 x1.43, 5 x1.75, never above 2.5x (the trait penalties do the heavy lifting)
AREA_NEAR_M = 500; AREA_NEAR_PEN = 2.0; STREET_PEN = 6.0; NOISE_STREET_PEN = 3.0; FB_MAX = 14.0; STYLE_MAX = 6.0
DEV_M, DEV_PEN = 120, 4.0           # same street + within ~120 m of ANY home marked 'not for us' (same development / builder)
TRAIT_PEN = (0, 7, 12, 16, 20)      # points off the score of a home sharing a trait you marked, by number of votes (4+ = 20)
GATE_PEN, GATE_N, TOTAL_MAX = 35.0, 2, 50.0   # a confirmed miss on a core requirement after 2+ votes: near-gate
CORE_GATE = {"openplan", "utility", "overlooked", "garden", "wc", "parking", "condition", "beds", "lounge"}
PLACE_PEN = 30.0
# free-text notes also count: map what was written to the same reasons (so a note-only 'not for us' still teaches something)
NOTE_MAP = [
    (r"(see|watch|spot|supervis)\w*\b.{0,40}\b(kids?|child|children)|\bopen[- ]?plan\b|\bkitchen\b.{0,30}\b(separate|closed off|not open)", "openplan"),
    (r"\b(lounge|living ?room|sitting ?room|reception)\b.{0,50}\b(1st|first|upstairs|top floor)\b|\b(1st|first|upstairs)\b.{0,25}\b(lounge|living)", "lounge"),
    (r"\bno (separate )?(lounge|living room)\b", "lounge"), (r"\bkitchen\b.{0,25}\b(small|tiny|cramped)", "kitchen_small"),
    (r"\butility\b", "utility"), (r"\boverlook", "overlooked"), (r"\bgarden\b.{0,20}\b(small|tiny)|\b(small|tiny)\b.{0,12}\bgarden", "garden"),
    (r"\b(parking|driveway|garage)\b", "parking"), (r"\b(dated|needs work|tired|renovat|refurb|modernis)", "condition"),
    (r"(don'?t|do not|never|not)\b.{0,25}\b(want|live|like)\b.{0,15}\b(in|near|around|on)\b|\b(area|estate|street|neighbourhood)\b", "area"),
    (r"\b(noise|noisy|busy road|main road|motorway)\b", "noise"), (r"\b(too far|commute|long drive)\b", "far"),
    (r"\b(overpriced|too expensive|expensive|poor value)\b", "price"),
]
_PLACE = re.compile(r"(?:live in|living in|move to|be in|near|around|avoid)\s+([A-Za-z][A-Za-z' -]{3,24}?)(?=$|[.,;!?]| as | because| it| which| and )", re.I)

_lock = threading.RLock()
_items = {}            # url -> {url, down, reasons, note, updated, snap, lat, lon, sk, lid}
_meta = {"reset": 0}
_ver = 0
_mult = {"key": None, "v": {}}
_style = {"key": None, "lo": 0, "hi": 0, "vecs": [], "ids": set()}

def _norm(u): return favs.norm_url(u)

def _clean(it, old=None):
    e = {"url": _norm(it.get("url")), "down": bool(it.get("down")), "updated": float(it.get("updated") or 0),
         "reasons": [r for r in (it.get("reasons") or []) if r in LABEL][:len(LABEL)], "note": str(it.get("note") or "")[:300]}
    s = it.get("snap") if isinstance(it.get("snap"), dict) else {}
    e["snap"] = {k: s.get(k) for k in ("id", "title", "address", "price", "image", "score", "bedrooms", "property_type", "source", "bathrooms", "band")}
    for k in ("lat", "lon", "sk"): e[k] = it.get(k) if it.get(k) is not None else (old or {}).get(k)
    return e

def _enrich(e):
    """server-side location of a downvoted home (used for the 'near / same street' penalty)"""
    if not e["down"] or (e.get("lat") is not None and e.get("sk") is not None): return
    r = db.q1("SELECT l.id, l.lat, l.lon, l.street_key FROM sightings s JOIN listings l ON l.id=s.listing_id WHERE s.url=? OR s.url LIKE ? LIMIT 1", (e["url"], e["url"] + "#%"))
    if r: e["lat"], e["lon"], e["sk"], e["lid"] = r["lat"], r["lon"], r["street_key"], r["id"]

def merge(client_items, meta=None):
    """last-write-wins per url (entries carry `updated`).  Returns the merged map."""
    global _ver
    with _lock:
        if meta and (meta.get("reset") or 0) > _meta["reset"]: _meta["reset"] = meta["reset"]
        for u, it in (client_items or {}).items():
            if not isinstance(it, dict): continue
            it = {**it, "url": it.get("url") or u}
            if "://" not in _norm(it["url"]): continue
            e = _clean(it, _items.get(_norm(it["url"])))
            old = _items.get(e["url"])
            if old is None or e["updated"] >= old["updated"]:
                _items[e["url"]] = e
        for e in _items.values(): _enrich(e)
        _ver += 1
        return dict(_items)

def export():
    with _lock: return {"items": dict(_items), "meta": dict(_meta)}

def active():
    with _lock: return [e for e in _items.values() if e["down"]]

def count(): return len(active())

def apply():
    """mirror the votes into listings.down (what every listing query filters on).  Returns True if anything changed."""
    urls = {e["url"] for e in active()}
    ids = set()
    if urls:
        for r in db.q("SELECT url, listing_id FROM sightings"):
            if _norm(r["url"]) in urls: ids.add(r["listing_id"])
    cur = {r["id"] for r in db.q("SELECT id FROM listings WHERE down=1")}
    if ids == cur: return False
    with db._write_lock:
        c = db.conn()
        c.execute("UPDATE listings SET down=0 WHERE down=1")
        c.executemany("UPDATE listings SET down=1 WHERE id=?", [(i,) for i in ids]); c.commit()
    return True

# ---------------- learning ----------------
def eff_reasons(e):
    """the reasons picked + any implied by the free-text note"""
    rs = set(e.get("reasons") or [])
    note = (e.get("note") or "").lower()
    if note:
        for rx, r in NOTE_MAP:
            if re.search(rx, note): rs.add(r)
    return rs

def note_places(e):
    """places named in a "don't want to live in X" style note"""
    note = (e.get("note") or "")
    if not re.search(r"(don'?t|do not|never|not|avoid)", note, re.I): return []
    return [m.group(1).strip().lower() for m in _PLACE.finditer(note) if len(m.group(1).strip()) >= 4 and m.group(1).strip().lower() not in ("the area", "this area", "that area", "here", "there")]

def _counts():
    out = {}
    for e in active():
        if e["updated"] / 1000.0 <= _meta["reset"]: continue
        for r in eff_reasons(e): out[r] = out.get(r, 0) + 1
    return out

def _m(n, cap=CAP): return 1 + (cap - 1) * n / (n + K)
def _mk(kb, n): return _m(n, 2.2) if kb == "w:openplan" else _m(n)      # the core criterion reacts harder to downvotes

def multipliers():
    """{knob: multiplier >= 1}"""
    key = (_ver, _meta["reset"])
    if _mult["key"] == key: return _mult["v"]
    with _lock:
        v = {}
        for r, n in _counts().items():
            for kb in KNOBS.get(r, []): v[kb] = max(v.get(kb, 1.0), _mk(kb, n))
        _mult["key"], _mult["v"] = key, v
    return v

def learned(cfg=None):
    cfg = cfg or db.config(); ckw = {k: s["weight"] for k, s in cfg["criteria"]["checklist"].items()}
    sw = cfg["criteria"]["score_weights"]; cnt = _counts(); rows = []
    for r, label in REASONS:
        n = cnt.get(r, 0); m = _m(n) if n else 1.0; eff = []
        for kb in KNOBS.get(r, []):
            kind, name = kb.split(":")
            base = ckw.get(name) if kind == "ck" else IDEAL_W.get(name) if kind == "ideal" else sw.get(name) if kind == "w" else ADJ_MAX.get(name)
            if base is None: continue
            eff.append({"name": KNOB_NAME.get(kb, kb), "default": base, "now": round(base * (_mk(kb, n) if n else 1.0), 2)})
        extra = {"area": "+ penalty for homes within ~500 m / on the same street", "noise": "penalty for homes on the same street",
                 "style": "penalty for homes whose photos look similar", "other": "recorded only"}.get(r)
        if n and r in TRAIT_SEV:
            tp = TRAIT_PEN[min(n, 4)]
            extra = (extra + "; " if extra else "") + f"homes with this trait lose ~{tp} pts" + (f"; a confirmed miss loses {GATE_PEN:.0f} (near-gate)" if n >= GATE_N and r in CORE_GATE else f" ({GATE_N - n} more vote{'s' if GATE_N - n != 1 else ''} makes it a near-gate)" if r in CORE_GATE else "")
        rows.append({"id": r, "label": label, "count": n, "mult": round(m, 2), "effects": eff, "extra": extra})
    return {"rows": rows, "total": count(), "counted": sum(1 for e in active() if e["updated"] / 1000.0 > _meta["reset"]), "cap": CAP, "reset": _meta["reset"]}

def reset():
    global _ver
    with _lock: _meta["reset"] = time.time(); _ver += 1

# ---------------- penalties for similar homes ----------------
def _hav(la1, lo1, la2, lo2):
    p = math.pi / 180; a = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(a))

def area_penalty(l):
    """-> (points >= 0, [why strings]) for a listing near / on the same street as homes marked 'area' or 'noise'"""
    near = 0.0; street = 0.0; why = []; devs = False
    lat, lon, sk = l.get("lat"), l.get("lon"), l.get("street_key")
    for e in active():
        if e["updated"] / 1000.0 <= _meta["reset"]: continue
        rs = eff_reasons(e)
        dev = bool(rs - {"price", "far", "other"}) or not rs      # any 'not for us' home: neighbours on the same street within ~120 m share builder/layout/traits
        if not ("area" in rs or "noise" in rs or dev): continue
        d = _hav(lat, lon, e["lat"], e["lon"]) if None not in (lat, lon, e.get("lat"), e.get("lon")) else None
        if d is not None and d < 15: continue                   # the same home
        same = bool(sk and e.get("sk") and sk == e["sk"] and (d is None or d <= 700))
        if same and ("area" in rs): street = max(street, STREET_PEN)
        elif same and ("noise" in rs): street = max(street, NOISE_STREET_PEN)
        elif same and d is not None and d <= DEV_M: street = max(street, DEV_PEN); devs = True
        elif "area" in rs and d is not None and d <= AREA_NEAR_M: near += AREA_NEAR_PEN
    pen = min(FB_MAX - 2, street + min(5.0, near))
    if street: why.append("same street / development as a home marked not for us")
    if near: why.append("close to homes marked 'area not for us'")
    return pen, why

def _style_state():
    key = (_ver, _meta["reset"], int(time.time() // 900))
    if _style["key"] == key: return _style
    with _lock:
        sv = []; ids = set()
        for e in active():
            if not (eff_reasons(e) & {"style", "layout", "condition"}) or e["updated"] / 1000.0 <= _meta["reset"]: continue
            if e.get("lid") is None: _enrich(e)
            if e.get("lid") is None: continue
            v = favs._mean_emb(e["lid"])
            if v is not None: sv.append(v); ids.add(e["lid"])
        lo = hi = 0.0
        if sv:
            raw = []
            for r in db.q("SELECT id FROM listings WHERE active=1 AND down=0"):
                v = favs._mean_emb(r["id"])
                if v is not None: raw.append(max(float(np.dot(v, s)) for s in sv))
            if len(raw) >= 10:
                raw.sort(); lo, hi = raw[int(len(raw) * 0.7)], raw[int(len(raw) * 0.97)]   # only the ~30% most alike are touched
        _style.update(key=key, lo=lo, hi=hi, vecs=sv, ids=ids)
    return _style

def style_penalty(lid):
    st = _style_state()
    if not st["vecs"] or lid in st["ids"] or st["hi"] <= st["lo"]: return 0.0
    v = favs._mean_emb(lid)
    if v is None: return 0.0
    s = max(float(np.dot(v, x)) for x in st["vecs"])
    p = STYLE_MAX * min(1.0, max(0.0, (s - st["lo"]) / (st["hi"] - st["lo"])))
    p *= len(st["vecs"]) / (len(st["vecs"]) + 1.5)          # damped a little: one vote is a nudge, five a firm push
    return round(p, 1) if p >= 0.3 else 0.0

def place_penalty(l):
    """homes in a place a note says you don't want to live in (e.g. "Don't want to live in X")"""
    hay = ((l.get("address") or "") + " " + (l.get("title") or "") + " " + (l.get("description") or "")[:1500]).lower()
    for e in active():
        if e["updated"] / 1000.0 <= _meta["reset"]: continue
        for p in note_places(e):
            if re.search(r"\b" + re.escape(p) + r"\b", hay): return PLACE_PEN, f"you said you don't want to live in {p.title()}"
    return 0.0, None

def trait_penalty(sev):
    """sev: {reason: severity 0..1} for this home (how strongly it has the trait).  -> {pen, why[], gate[]}.
    Penalty only: n votes for a reason take TRAIT_PEN[n] x severity points off homes sharing the trait; after GATE_N votes a
    confirmed miss on a core requirement takes GATE_PEN off (near-gate).  Reset clears it."""
    cnt = _counts(); pen = 0.0; why = []; gate = []
    for r, s in sev.items():
        n = cnt.get(r, 0)
        if not n or s <= 0: continue
        p = TRAIT_PEN[min(n, 4)] * s
        if n >= GATE_N and s >= 0.95 and r in CORE_GATE: p = max(p, GATE_PEN); gate.append(r)
        if p >= 0.5: pen += p; why.append(f"{LABEL.get(r, r)} ({n} vote{'s' if n != 1 else ''}) \u2212{p:.0f}")
    return {"pen": round(min(TOTAL_MAX, pen), 1), "why": why, "gate": gate}

def feedback(l):
    """{pen, why[]} for evaluate(): additive penalty from the votes (0 when there are none)"""
    if not active(): return None
    pen, why = area_penalty(l)
    pp, pw = place_penalty(l)
    if pp: pen += pp; why.append(pw)
    sp = style_penalty(l["id"]) if l.get("id") else 0.0
    if sp: pen += sp; why.append("photos look like homes marked 'style not for us'")
    pen = round(min(FB_MAX + (PLACE_PEN if pp else 0), pen), 1)
    return {"pen": pen, "why": why} if pen else None

# ---------------- pre-highlighted pills ----------------
def suspects(ev):
    """reason ids the scorer already suspects for this home (shown highlighted, never pre-selected)"""
    if not ev: return []
    out = []
    rows = {r["key"]: r for r in (ev.get("vs_ideal") or {}).get("rows", [])}
    def short(*ks): return any((rows.get(k) or {}).get("status") == "short" for k in ks)
    if short("kitchen_family") or (ev.get("open_plan") or {}).get("state") in ("absent", "unconfirmed"): out.append("openplan")
    if short("lounge"): out.append("lounge")
    if short("kitchen_area"): out.append("kitchen_small")
    if short("utility"): out.append("utility")
    if short("wc", "ensuite"): out.append("wc")
    if short("bedrooms", "bed_sizes"): out.append("beds")
    if short("garden"): out.append("garden")
    if (ev.get("checklist", {}).get("not_overlooked") or {}).get("state") == "contradicted": out.append("overlooked")
    if short("parking"): out.append("parking")
    c = (ev.get("condition") or {}).get("state")
    if c in ("needs_work", "some_updating"): out.append("condition")
    cr = ev.get("crime") or {}
    if cr.get("level") in ("high", "review"): out.append("area")
    if any(h["key"].startswith("anchor:") and not h["ok"] and h.get("status") != "unknown" for h in ev.get("hard") or []): out.append("far")
    if ev.get("band") == "just_over": out.append("price")
    n = (ev.get("vs_ideal") or {}).get("counts", {}).get("short", 0)
    if n >= 4 and "layout" not in out: out.append("layout")
    return out
