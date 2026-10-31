"""Polling pipeline: fetch -> dedupe -> geo/routes -> analyse -> evaluate -> notify."""
import os, json, re, threading, time, traceback
from pathlib import Path
import numpy as np
from . import db, geo, http, textanalysis, layout, embed, vision, refs, notify, stations, push, favs, cache, avoid, crime, condition, snapshot, votes
from .config import DATA
from .engine_io import fetch_file
from .matcher import evaluate, listing_text
from .sources import make_source
from .sources.base import blank

state = {"running": False, "started": None, "finished": None, "progress": [], "last_error": None, "trigger": None, "next_run": None}
_poll_lock = threading.Lock()
JSONF = ("features", "images", "floorplans", "extra")

def log(msg):
    state["progress"].append(f"{time.strftime('%H:%M:%S')} {msg}")
    state["progress"] = state["progress"][-80:]
cache.logger = log

# ---------------- dedupe / ingest ----------------
def _ts(s):
    """ISO string / dd/mm/yyyy / epoch -> epoch seconds (None when unreadable)"""
    if s is None or s == "": return None
    if isinstance(s, (int, float)): return float(s) / (1000.0 if s > 1e11 else 1.0)
    s = str(s).strip()
    try:
        import datetime as _dt
        if re.match(r"\d{1,2}/\d{1,2}/\d{4}", s): return _dt.datetime.strptime(s[:10], "%d/%m/%Y").replace(tzinfo=_dt.timezone.utc).timestamp()
        d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00")[:32])
        return (d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)).timestamp()
    except Exception: return None

def fresh_info(l, now=None):
    """When was this home listed / last (re)listed or reduced, per Rightmove's own dates (first-seen only as a labelled fallback)."""
    now = now or time.time()
    ex = l.get("extra") or {}
    lt = _ts(l.get("listed_at")); ru = ex.get("rm_update") or {}; ut = _ts(ru.get("ts"))
    reason = ru.get("reason") if ut else None
    basis = "listed" if lt else "seen"
    base = lt or l.get("first_seen")
    ft = max([t for t in (lt, ut) if t] or [l.get("first_seen") or now])
    return {"listed_ts": lt, "update_reason": reason, "update_ts": ut, "fresh_ts": ft, "basis": basis,
            "age_days": int(max(0, now - base) // 86400) if base else None,
            "update_days": int(max(0, now - ut) // 86400) if ut else None}

def street_key(addr):
    a = (addr or "").split(",")[0].lower()
    a = re.sub(r"\b(flat|apartment|plot|unit)\s*\w+\b", "", a)
    a = re.sub(r"\b\d+[a-z]?\b", "", a)
    return re.sub(r"[^a-z ]", "", a).strip()

def _row(l):
    d = dict(l)
    for k in JSONF: d[k] = json.loads(d[k]) if isinstance(d.get(k), str) else (d.get(k) or ([] if k != "extra" else {}))
    return d

def get_listing(lid):
    r = db.q1("SELECT * FROM listings WHERE id=?", (lid,))
    return _row(r) if r else None

def find_match(n):
    s = db.q1("SELECT listing_id FROM sightings WHERE source=? AND source_id=?", (n["source"], n["source_id"]))
    if s: return s["listing_id"], True
    sk = street_key(n["address"])
    beds = n.get("bedrooms"); price = n.get("price")
    for c in db.q("SELECT id,bedrooms,price,lat,lon,street_key,postcode FROM listings WHERE active=1"):
        if beds and c["bedrooms"] and beds != c["bedrooms"]: continue
        if price and c["price"] and abs(price - c["price"]) / max(price, c["price"]) > 0.06: continue
        dist = None
        if n.get("lat") and c["lat"]: dist = geo.haversine_km(n["lat"], n["lon"], c["lat"], c["lon"]) * 1000
        if dist is not None and dist <= 15: return c["id"], False
        same_street = sk and c["street_key"] and sk == c["street_key"]
        if same_street and (dist is None and (not n.get("postcode") or not c["postcode"] or n["postcode"].replace(" ", "") == c["postcode"].replace(" ", ""))
                            or (dist is not None and dist <= 400)):
            return c["id"], False
    return None, False

def _union(a, b):
    return list(dict.fromkeys([x for x in (a or []) + (b or []) if x]))

def ingest(n):
    now = time.time()
    lid, known = find_match(n)
    if lid is None:
        cur = db.x("""INSERT INTO listings(title,address,postcode,lat,lon,price,price_text,bedrooms,bathrooms,property_type,tenure,description,
                      features,images,floorplans,agent,status_text,listed_at,extra,first_seen,last_seen,detail_done,street_key)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (n["title"], n["address"], n["postcode"], n["lat"], n["lon"], n["price"], n["price_text"], n["bedrooms"], n["bathrooms"],
                    n["property_type"], n["tenure"], n["description"], json.dumps(n["features"]), json.dumps(n["images"]), json.dumps(n["floorplans"]),
                    n["agent"], n["status_text"], n["listed_at"], json.dumps(n["extra"]), now, now, int(n["detail_done"]), street_key(n["address"])))
        lid = cur.lastrowid; is_new = True
    else:
        is_new = False
        cur = get_listing(lid)
        upd = {}
        for k in ("lat", "lon", "postcode", "bedrooms", "bathrooms", "property_type", "tenure", "price", "price_text", "listed_at"):
            if (cur.get(k) in (None, "", 0)) and n.get(k): upd[k] = n[k]
        if len(n.get("description") or "") > len(cur.get("description") or ""): upd["description"] = n["description"]
        if len(n["title"] or "") > len(cur["title"] or "") and not cur["title"]: upd["title"] = n["title"]
        if known and n.get("price") and cur.get("price") != n["price"] and n["source"] == (db.q1("SELECT source FROM sightings WHERE listing_id=? ORDER BY id LIMIT 1", (lid,)) or {}).get("source"):
            upd["price"] = n["price"]; upd["price_text"] = n["price_text"]
        if n.get("agent") and n["source"] not in ("rightmove", "onthemarket", "purplebricks", "zoopla"): upd["agent"] = n["agent"]
        elif n.get("agent") and not cur.get("agent"): upd["agent"] = n["agent"]
        feats = _union(cur["features"], n["features"]); imgs = _union(cur["images"], n["images"])
        fps = _union(cur["floorplans"], n["floorplans"])
        if n["detail_done"]: imgs = _union(n["images"], cur["images"]) if known else imgs
        upd.update(features=json.dumps(feats), images=json.dumps(imgs[:60]), floorplans=json.dumps(fps[:6]),
                   extra=json.dumps({**cur["extra"], **n["extra"]}), last_seen=now, active=1,
                   street_key=street_key(n["address"]) or cur.get("street_key"))
        if n["detail_done"]: upd["detail_done"] = 1
        if n.get("status_text"): upd["status_text"] = n["status_text"]
        sets = ", ".join(f"{k}=?" for k in upd)
        db.x(f"UPDATE listings SET {sets} WHERE id=?", (*upd.values(), lid))
    db.x("""INSERT INTO sightings(listing_id,source,source_id,url,price,first_seen,last_seen,raw) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(source,source_id) DO UPDATE SET last_seen=excluded.last_seen, price=excluded.price, listing_id=excluded.listing_id,
            url=excluded.url, raw=CASE WHEN excluded.raw LIKE '%"detail_done": true%' THEN excluded.raw ELSE sightings.raw END""",
         (lid, n["source"], n["source_id"], n["url"], n["price"], now, now, json.dumps({"detail_done": n["detail_done"]})))
    return lid, is_new

# ---------------- candidate gate ----------------
def is_candidate(l, cfg):
    c = cfg["criteria"]
    if l.get("price") and not (c["price_min"] * 0.9 <= l["price"] <= c["price_stretch_max"]): return False
    if l.get("bedrooms") and l["bedrooms"] < c["bedrooms_min"]: return False
    st = (l.get("status_text") or "").lower()
    if re.search(r"sold|\bs?stc\b|under offer|sale agreed|reserved|withdrawn|unpublished", st): return False
    if avoid.check(l, c.get("avoid_areas")): return False      # private list of areas to avoid: don't spend calls on them
    ok = True
    t = ((l.get("property_type") or "") + " " + (l.get("title") or "")).lower()
    if any(b in t for b in c.get("exclude_property_types", [])): return False
    if l.get("lat") is not None:
        for a in c["anchors"]:
            if geo.haversine_km(l["lat"], l["lon"], a["lat"], a["lon"]) > a["max_straight_km"]: return False
    return ok

# ---------------- analysis ----------------
def img_dir(lid):
    d = DATA / "images" / str(lid); d.mkdir(parents=True, exist_ok=True); return d

def _release_memory():
    """give freed heap back to the OS (matters on a 512 MB host)"""
    import gc
    gc.collect()
    try:
        import ctypes; ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception: pass

def analyse_listing(lid, cfg, force=False):
    l = get_listing(lid)
    old = db.q1("SELECT data FROM analysis WHERE listing_id=?", (lid,))
    sig = f"{len(l['description'])}|{len(l['images'])}|{len(l['floorplans'])}|{len(l['features'])}"
    prev = json.loads(old["data"]) if old else {}
    if prev.get("sig") == sig and not force: return prev
    if not force:
        hit = cache.restore_analysis(lid, l, sig)     # floor-plan OCR + photo vectors from the persistent cache
        if hit: return hit
    an = {"sig": sig, "updated": time.time()}
    base_text = listing_text(l)
    d = img_dir(lid)
    # floorplans -> OCR
    fpres, fpfiles, fpurls = [], [], []
    if layout.ocr_available():
        for i, u in enumerate(l["floorplans"][: (2 if os.environ.get("PROPDASH_LEAN") == "1" else 3)]):
            p = fetch_file(u, d / f"floorplan{i}.jpg", 0.2)
            if p: fpfiles.append(p); fpurls.append(u)
        for p, (r, err) in zip(fpfiles, layout.analyse_floorplans(fpfiles)):
            if r: fpres.append(r)
            if err: an.setdefault("errors", []).append(f"OCR {p.name}: {err}")
        fpfiles = [p.name for p in fpfiles]
    an["floorplan_files"] = fpfiles; an["floorplan_urls"] = fpurls
    fp = layout.combine(fpres) if fpres else None
    if fp: 
        an["floorplan"] = fp; an["floorplan_text"] = layout.floorplan_text(fp)
    an["text"] = textanalysis.evaluate_text(base_text + "\n" + an.get("floorplan_text", ""))
    # photos -> embeddings (+ vision)
    photos, photo_urls = [], []
    if cfg["poll"].get("analyse_photos_for_candidates", True):
        for i, u in enumerate(l["images"][: cfg["poll"].get("max_photos_per_listing", 12)]):
            p = fetch_file(u, d / f"photo{i}.jpg", 0.15)
            if p: photos.append(p); photo_urls.append(u)
        embs = embed.embed_many(photos)
        embs = [e for e in embs if e is not None]
        if embs: np.save(d / "emb.npy", np.stack(embs))
        an["n_photos"] = len(photos); an["embed_backend"] = embed.status["backend"]
    an["photo_files"] = [p.name for p in photos]; an["photo_urls"] = photo_urls
    vcfg = cfg.get("vision", {})
    if vision.available(vcfg):
        paths = [d / f for f in fpfiles] + photos
        v = vision.analyse(vcfg, paths, base_text)
        an["vision"] = v
        if v.get("answers"): an["vision_checklist"] = vision.to_checklist(v["answers"])
    else:
        an["vision"] = {"available": False, "reason": "no API key configured (set PROPDASH_VISION_API_KEY and enable in Settings)"}
    db.x("INSERT OR REPLACE INTO analysis(listing_id,data,updated) VALUES(?,?,?)", (lid, json.dumps(an), time.time()))
    cache.record(lid, analysed=True)
    return an

def listing_embs(lid):
    f = DATA / "images" / str(lid) / "emb.npy"
    if f.exists():
        a = np.load(f); return [a[i] for i in range(a.shape[0])]
    return []

# ---------------- routes ----------------
def compute_routes(l, cfg, do_route=True):
    if l.get("lat") is None:
        g = geo.geocode_postcode(l.get("postcode") or "") or (geo.geocode_postcode(l.get("address") or ""))
        if g:
            db.x("UPDATE listings SET lat=?,lon=? WHERE id=?", (g["lat"], g["lon"], l["id"])); l["lat"], l["lon"] = g["lat"], g["lon"]
            l["extra"]["coords_approx"] = "postcode centroid"
    if l.get("lat") is None: return {}
    out = {}
    for a in cfg["criteria"]["anchors"]:
        s = geo.haversine_km(l["lat"], l["lon"], a["lat"], a["lon"])
        prev = db.q1("SELECT * FROM routes WHERE listing_id=? AND anchor=?", (l["id"], a["name"]))
        if s <= a["max_straight_km"] and do_route:
            row = cache.cached_route(l, a)
            if row: row = {**row, "straight_km": s}
            else:
                r = geo.route(l["lat"], l["lon"], a["lat"], a["lon"])
                row = {"straight_km": s, "drive_min": r["drive_min"], "drive_km": r["drive_km"], "method": r["method"]}
        elif prev and abs(prev["straight_km"] - s) < 0.05:
            row = {k: prev[k] for k in ("straight_km", "drive_min", "drive_km", "method")}
        else:
            row = {"straight_km": s, "drive_min": s * 1.35 / 42 * 60 + 2, "drive_km": s * 1.35, "method": "estimate"}
        db.x("INSERT OR REPLACE INTO routes(listing_id,anchor,straight_km,drive_min,drive_km,method) VALUES(?,?,?,?,?,?)",
             (l["id"], a["name"], row["straight_km"], row["drive_min"], row["drive_km"], row["method"]))
        out[a["name"]] = row
    return out

def get_routes(lid):
    return {r["anchor"]: r for r in db.q("SELECT * FROM routes WHERE listing_id=?", (lid,))}

# ---------------- evaluation ----------------
def evaluate_listing(lid, cfg, refset=None, analyse=True):
    l = get_listing(lid)
    an = None
    a = db.q1("SELECT data FROM analysis WHERE listing_id=?", (lid,))
    if a: an = json.loads(a["data"])
    if l.get("lat") is not None:
        l["_station"] = stations.nearest(l["lat"], l["lon"])
    ck_for_sim = None
    if l.get("lat") is not None:        # cached crime context only (lookups happen in the poll, for homes that pass the hard filters)
        try: l["_crime"] = crime.rate(crime.lookup(l["lat"], l["lon"], fetch_missing=False))
        except Exception: pass
    try: l["_photo_cond"] = condition.photo_condition(listing_embs(lid))
    except Exception: pass
    try: l["_fb"] = votes.feedback(l)       # small penalty from 'not for us' votes (near / same street / similar photos)
    except Exception: pass
    ev = evaluate(l, get_routes(lid), an, cfg, None, refs.current_home())
    sim = None
    if refset is not None:
        sim = refs.similarity(listing_embs(lid), ev["checklist"], refset)
        if sim: ev = evaluate(l, get_routes(lid), an, cfg, sim, refs.current_home())
    db.x("INSERT OR REPLACE INTO evaluation(listing_id,data,score,band,passes,updated) VALUES(?,?,?,?,?,?)",
         (lid, json.dumps(ev), ev["score"], ev["band"], int(ev["passes"]), time.time()))
    return ev

def message_for(l, ev, kind):
    tag = "JUST OVER BUDGET · " if kind == "just_over" else "WORTH A LOOK · " if kind == "worth" else ""
    d = ev["drive"]
    drv = " · ".join(f'{k.split(" - ")[-1][:22]} {v["drive_min"]:.0f}m' for k, v in d.items())
    return {"title": f'{tag}New {"home" if kind == "worth" else "match"}: {l["bedrooms"]} bed, £{l["price"]:,} – {l["address"][:50]}',
            "body": f'Score {ev["score"]:.0f}/100 · {drv}' + (f' · check: {", ".join(ev["checklist"][k]["label"] for k in ev["unconfirmed_must"])}' if ev["unconfirmed_must"] else "") + (" · open-plan unconfirmed" if (ev.get("open_plan") or {}).get("state") == "unconfirmed" else ""),
            "kind": kind, "listing_id": l["id"], "price": l["price"], "address": l["address"]}

def curated(cfg=None):
    """Curated top lists. Returns {"matches": [ids best-first], "just_over": [...], "passing": {"matches": n, "just_over": n}}.
    A listing qualifies only if it passes every hard filter with no contradicted must-have (evaluation.passes)
    and its score reaches the configured cutoff; each list is then capped."""
    cfg = cfg or db.config()
    tm = cfg["criteria"].get("top_matches") or {}
    rows = db.q("""SELECT e.listing_id id, e.score s, e.band b, json_extract(e.data, '$.open_plan.state') op FROM evaluation e JOIN listings l ON l.id=e.listing_id
                   WHERE l.active=1 AND l.hidden=0 AND l.down=0 AND e.passes=1
                   AND l.last_seen >= (SELECT COALESCE(MAX(last_seen), 0) FROM listings) - 22 * 3600 ORDER BY e.score DESC""")   # a home the searches stopped returning ~a day ago has probably gone under offer / been withdrawn
    out = {"matches": [], "just_over": [], "worth": [], "passing": {"matches": 0, "just_over": 0}}
    try: bst = favs.boosts()      # modest 'similar to your saved homes' nudge (passing listings only, capped)
    except Exception: bst = {}
    rows = sorted(({**r, "s": (r["s"] or 0) + bst.get(r["id"], 0)} for r in rows), key=lambda r: -r["s"])
    taken = set()
    for key, band, ms, mc in (("matches", "in_budget", tm.get("min_score", 60), tm.get("max_count", 10)),
                              ("just_over", "just_over", tm.get("just_over_min_score", 62), tm.get("just_over_max_count", 5))):
        pool = [r for r in rows if r["b"] == band]
        out["passing"][key] = len(pool)
        # Top lists need a CONFIRMED open-plan kitchen/dining/family space; unconfirmed ones go to 'Worth a look'
        ok = [r["id"] for r in pool if r.get("op") == "confirmed" and (r["s"] or 0) >= (ms or 0)]
        out[key] = ok[: int(mc)] if mc else ok
        taken.update(out[key])
    # 'Worth a look': passes every hard filter (open-plan not clearly absent) but open-plan / other core items are unconfirmed or the score is just under the cut-off
    wm, wc = tm.get("worth_min_score", 40), tm.get("worth_max_count", 15)
    out["worth"] = [r["id"] for r in rows if r["id"] not in taken and (r["s"] or 0) >= (wm or 0)][: int(wc) if wc else None]
    return out

def notify_curated(cfg, baseline):
    """Notifications are raised ONLY for listings in the curated top lists."""
    cur = curated(cfg)
    for key in ("matches", "just_over", "worth"):
        for lid in cur[key]:
            e = db.q1("SELECT data FROM evaluation WHERE listing_id=?", (lid,))
            if e: maybe_notify(lid, json.loads(e["data"]), cfg, baseline, "worth" if key == "worth" else None)

def maybe_notify(lid, ev, cfg, baseline, force_kind=None):
    c = cfg["criteria"]
    if not ev["passes"] or ev["score"] < c.get("min_score_to_notify", 0): return
    kind = force_kind or ("match" if ev["band"] == "in_budget" else "just_over")
    if kind == "just_over" and not c.get("notify_just_over_budget", True): return
    if db.q1("SELECT id FROM notifications WHERE listing_id=? AND kind=?", (lid, kind)): return
    l = get_listing(lid)
    if not baseline and fresh_info(l)["fresh_ts"] < time.time() - 3 * 86400: return     # alerts are for NEW (or newly reduced / re-listed) homes: an old home that only just entered the lists through scoring stays quiet
    n = message_for(l, ev, kind)
    db.x("INSERT OR IGNORE INTO notifications(listing_id,kind,message,created,baseline) VALUES(?,?,?,?,?)",
         (lid, kind, json.dumps(n), time.time(), int(baseline)))
    n["url"] = f"/#/listing/{lid}"
    res = notify.dispatch(cfg["notifiers"], n, baseline=baseline)
    try:
        if not baseline: push.send_all(n)
    except Exception as e:
        log(f"web push failed: {e}")
    for name, ok, msg in res:
        if not ok and str(msg).startswith("baseline batch"): continue
        log(f"notify[{name}] {'ok' if ok else 'skip/fail'}: {msg}")

# ---------------- poll ----------------
def set_status(sid, **kw):
    old = db.q1("SELECT data FROM source_status WHERE source=?", (sid,))
    d = json.loads(old["data"]) if old else {}
    d.update(kw)
    db.x("INSERT OR REPLACE INTO source_status(source,data) VALUES(?,?)", (sid, json.dumps(d)))

def run_poll(trigger="manual"):
    if not _poll_lock.acquire(blocking=False): return False
    try:
        state.update(running=True, started=time.time(), trigger=trigger, last_error=None, progress=[])
        cfg = db.config()
        cache.load()      # persistent analysis cache (no-op if not configured / unreachable)
        baseline = not db.get_setting("baseline_done", False)
        log("poll started" + (" (baseline run: first batch is not pushed externally)" if baseline else ""))
        try: refs.scan_folder()
        except Exception as e: log(f"reference scan failed: {e}")
        crit, poll = cfg["criteria"], cfg["poll"]
        ensure_anchor_coords(cfg)
        cfg = db.config(); crit = cfg["criteria"]
        pending = []   # (source_obj, normalized listing)
        ok_sources = 0
        for sc in cfg["sources"]:
            sid = sc["id"]
            if not sc.get("enabled"):
                set_status(sid, state="disabled", name=sc["name"], type=sc["type"], url=sc.get("url")); continue
            t0 = time.time()
            try:
                src = make_source(sc)
                res = src.search(crit, poll)
                ids = []
                for n in res:
                    n["agent"] = n.get("agent") or sc["name"]
                    n = cache.apply_detail(n)
                    lid, is_new = ingest(n); ids.append((lid, n["source_id"]))
                    if is_new: cache.apply_hidden(lid)
                    pending.append((src, n["source"], n["source_id"], lid))
                set_status(sid, state="ok", name=sc["name"], type=sc["type"], url=sc.get("url"), last_run=time.time(), count=len(res),
                           message=f"{len(res)} listings fetched" + (f" ({len(src.warnings)} warnings)" if src.warnings else ""),
                           warnings=src.warnings[:6], seconds=round(time.time() - t0, 1), http_status=200, reason=None)
                log(f"{sid}: {len(res)} listings"); ok_sources += 1
            except http.Blocked as e:
                set_status(sid, state="blocked", name=sc["name"], type=sc["type"], url=sc.get("url"), last_run=time.time(), count=0,
                           message=e.reason, reason=e.reason, http_status=e.status, seconds=round(time.time() - t0, 1))
                log(f"{sid}: BLOCKED - {e.reason}")
            except Exception as e:
                set_status(sid, state="error", name=sc["name"], type=sc["type"], url=sc.get("url"), last_run=time.time(), count=0,
                           message=f"{type(e).__name__}: {e}", reason=str(e), seconds=round(time.time() - t0, 1))
                log(f"{sid}: ERROR {type(e).__name__}: {e}")
        # detail fetch for candidates
        budget = poll.get("max_detail_fetches_per_cycle", 80); fetched = 0
        srcmap = {sc["id"]: sc for sc in cfg["sources"]}
        for src, sname, sid_, lid in pending:
            if fetched >= budget: break
            s = db.q1("SELECT raw FROM sightings WHERE source=? AND source_id=?", (sname, sid_))
            if s and json.loads(s["raw"] or "{}").get("detail_done"): continue
            l = get_listing(lid)
            if not is_candidate(l, cfg): continue
            sg = db.q1("SELECT url FROM sightings WHERE source=? AND source_id=?", (sname, sid_))
            n = blank(sname, sid_, sg["url"])
            n.update({k: l[k] for k in ("title", "address", "postcode", "lat", "lon", "price", "price_text", "bedrooms", "bathrooms", "property_type", "tenure", "description", "agent", "status_text")})
            n["features"], n["images"], n["floorplans"], n["extra"] = list(l["features"]), list(l["images"]), list(l["floorplans"]), dict(l["extra"])
            try:
                n = src.fetch_detail(n); fetched += 1
                ingest(n)
            except http.Blocked as e:
                log(f"detail blocked {sname}: {e.reason}")
            except Exception as e:
                log(f"detail failed {sname} {sid_}: {e}")
        log(f"detail pages fetched: {fetched}")
        try: votes.apply()
        except Exception as e: log(f"votes apply failed: {e}")
        # evaluate everything active
        refset = refs.reference_set()
        if not refset[0] and not refset[1]: refset = None
        ids = [r["id"] for r in db.q("SELECT id FROM listings WHERE active=1")]
        n_cand = 0; crime_ids = []
        for lid in ids:
            l = get_listing(lid)
            try:
                if is_candidate(l, cfg) or l.get("lat") is None:
                    compute_routes(l, cfg)
                    l = get_listing(lid)
                if is_candidate(l, cfg):
                    pre = evaluate_listing(lid, cfg, None)
                    hard_ok = all(h["ok"] for h in pre["hard"] if h["key"] not in ("must_haves", "crime"))
                    if hard_ok:
                        n_cand += 1
                        if l.get("lat") is not None and crime.lookup(l["lat"], l["lon"]): crime_ids.append(lid)
                        analyse_listing(lid, cfg)
                        _release_memory()
                evaluate_listing(lid, cfg, refset)
            except Exception as e:
                log(f"evaluate {lid} failed: {type(e).__name__}: {e}")
                traceback.print_exc()
        log(f"analysed {n_cand} candidates in depth")
        try:      # the typical-crime baseline moved while we fetched: score the homes that have crime data again
            for lid in crime_ids: evaluate_listing(lid, cfg, refset)
            log(f"crime: {crime.stats['fetched']} places fetched, {crime.stats['cached']} from cache, {crime.stats['failed']} failed, {crime.stats['calls']} API calls")
        except Exception as e: log(f"crime re-score failed: {e}")
        try: state["filters"] = filter_counts()
        except Exception as e: log(f"filter counts failed: {e}")
        notify_curated(cfg, baseline)
        cur = curated(cfg)
        log(f"top matches: {len(cur['matches'])} (of {cur['passing']['matches']} passing) · just over budget: {len(cur['just_over'])} (of {cur['passing']['just_over']} passing)")
        # deactivate stale
        db.x("UPDATE listings SET active=0 WHERE id NOT IN (SELECT listing_id FROM sightings WHERE last_seen > ?)", (time.time() - 3 * 86400,))
        if baseline:
            db.set_setting("baseline_done", True)
            db.x("UPDATE notifications SET read=1 WHERE baseline=1")  # first-run backlog is browsable but not an unread badge
        state.update(finished=time.time())
        db.set_setting("last_poll", {"finished": time.time(), "trigger": trigger})
        log("poll finished")
        state.pop("restored_at", None)      # the saved results have now been refreshed
        try:
            cache.record_all()
            st = cache.status()
            if st["enabled"]: log(f"cache: {st['entries']} entries, analysis hits {st['hits_analysis']}, misses {st['misses_analysis']}, detail hits {st['hits_detail']}, writes {st['writes']}" + (f", error {st['last_error']}" if st["last_error"] else ""))
        except Exception as e: log(f"cache save failed: {e}")
        try: snapshot.save(ok_sources)      # only reached after a completed poll: a partial poll never overwrites a good snapshot
        except Exception as e: log(f"snapshot save failed: {e}")
        return True
    except Exception as e:
        state["last_error"] = f"{type(e).__name__}: {e}"; traceback.print_exc(); log("poll crashed: " + state["last_error"]); return False
    finally:
        state["running"] = False
        _poll_lock.release()

def ensure_anchor_coords(cfg):
    changed = False
    for a in cfg["criteria"]["anchors"]:
        if a.get("lat") is None and a.get("address"):
            g = geo.geocode_any(a["address"])
            if g: a["lat"], a["lon"] = g["lat"], g["lon"]; changed = True
    if changed: db.set_setting("config", cfg)

def filter_counts():
    """counts of active listings removed / flagged by the area, condition and crime filters (no personal detail)"""
    out = {"avoided_area": 0, "needs_work": 0, "crime_review": 0, "crime_high": 0, "crime_low": 0, "condition_penalised": 0, "condition_boosted": 0, "no_open_plan": 0, "open_plan_unconfirmed": 0, "avoided_by": {}}
    for r in db.q("SELECT e.data d FROM listings l JOIN evaluation e ON e.listing_id=l.id WHERE l.active=1"):
        ev = json.loads(r["d"]); ex = ev.get("excluded") or []
        if "avoided area" in ex:
            out["avoided_area"] += 1
            for h in ev["hard"]:
                if h["key"] == "avoided area": out["avoided_by"][h["detail"]] = out["avoided_by"].get(h["detail"], 0) + 1
        if "needs work" in ex: out["needs_work"] += 1
        if "no open-plan" in ex: out["no_open_plan"] += 1
        elif (ev.get("open_plan") or {}).get("state") == "unconfirmed": out["open_plan_unconfirmed"] += 1
        cr = ev.get("crime")
        if cr: out["crime_" + cr["level"]] = out.get("crime_" + cr["level"], 0) + 1
        a = (ev.get("adjust") or {}).get("condition", 0)
        if a < 0: out["condition_penalised"] += 1
        elif a > 0: out["condition_boosted"] += 1
    return out

_rs = {"t": None}
def rescore_all():
    """re-score every active listing (no notifications): used after votes change the learned weights"""
    if not _poll_lock.acquire(blocking=False): return      # a poll is running: it re-scores everything at the end anyway
    try:
        cfg = db.config(); refset = refs.reference_set()
        if not refset[0] and not refset[1]: refset = None
        for r in db.q("SELECT id FROM listings WHERE active=1"):
            try: evaluate_listing(r["id"], cfg, refset)
            except Exception: traceback.print_exc()
    finally: _poll_lock.release()

def rescore_soon(delay=6):
    t = _rs["t"]
    if t: t.cancel()
    _rs["t"] = threading.Timer(delay, rescore_all); _rs["t"].daemon = True; _rs["t"].start()

def re_evaluate_all():
    cfg = db.config(); refset = refs.reference_set()
    if not refset[0] and not refset[1]: refset = None
    for r in db.q("SELECT id FROM listings WHERE active=1"):
        try:
            l = get_listing(r["id"])
            if l.get("lat") is not None: compute_routes(l, cfg, do_route=False)
            evaluate_listing(r["id"], cfg, refset)
        except Exception: traceback.print_exc()
    try: notify_curated(cfg, not db.get_setting("baseline_done", False))
    except Exception: traceback.print_exc()

_sched = {"thread": None, "stop": False}
def scheduler_loop():
    time.sleep(3)
    while not _sched["stop"]:
        cfg = db.config()
        interval = max(10, int(cfg["poll"].get("interval_minutes", 45))) * 60
        last = (db.get_setting("last_poll") or {}).get("finished", 0)
        due = last + interval
        state["next_run"] = due if (cfg["poll"].get("enabled", True) and last and not state["running"]) else None
        if cfg["poll"].get("enabled", True) and time.time() >= due and not state["running"]:
            run_poll("scheduled")
        time.sleep(20)

def start_scheduler():
    if _sched["thread"]: return
    t = threading.Thread(target=scheduler_loop, daemon=True); t.start(); _sched["thread"] = t
