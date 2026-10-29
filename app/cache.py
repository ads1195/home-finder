"""Persistent analysis cache.  Render's free disk is wiped whenever the app sleeps or redeploys, so the expensive results
(listing detail, floor-plan OCR, CLIP photo vectors, drive times) are kept as ONE gzipped JSON file in a private GitHub repo
and loaded again at start-up.  Configure with CACHE_REPO ("owner/name") and CACHE_TOKEN; if either is missing, or GitHub is
unreachable, everything silently behaves as before (no cache)."""
import os, io, gzip, json, time, base64, hashlib, threading
import numpy as np
import httpx
from . import db
from .config import DATA

REPO = os.environ.get("CACHE_REPO", "").strip()
TOKEN = os.environ.get("CACHE_TOKEN", "").strip()
PATH = os.environ.get("CACHE_PATH", "cache.json.gz")
ENABLED = bool(REPO and TOKEN)
PRUNE_DAYS = 60
DETAIL_TTL = 10 * 86400          # re-fetch a cached listing's detail page after this long
BATCH = 25                       # write back after this many new analyses
MIN_GAP = 600                    # ...and never more often than this (seconds), except at the end of a poll

logger = print                   # engine.py swaps in its progress log
_lock = threading.RLock()
_wlock = threading.Lock()        # one writer at a time
D = {"v": 1, "w": 0, "entries": {}, "hidden": {}, "favs": {}, "crime": {}}
_idx = {}                        # "source:source_id" -> entry id
_sha = None
_loaded = False
_new = 0                         # analyses recorded since the last write
_dirty = False
_last_try = 0.0
_timer = None
stats = {"enabled": ENABLED, "loaded": 0, "hits_detail": 0, "hits_analysis": 0, "misses_analysis": 0, "writes": 0, "last_error": None, "last_write": None, "bytes": 0}

def _log(m):
    try: logger(f"cache: {m}")
    except Exception: pass

# ---------------- GitHub I/O ----------------
def _hdr(raw=False):
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "home-finder-cache"}

def _blob_sha(b): return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()

def _fetch():
    """-> (data dict | None, sha | None).  Raises on transport / auth errors (404 = file not there yet)."""
    r = httpx.get(f"https://api.github.com/repos/{REPO}/contents/{PATH}", headers=_hdr(True), timeout=90, follow_redirects=True)
    if r.status_code == 404: return None, None
    r.raise_for_status()
    raw = r.content
    d = json.loads(gzip.decompress(raw))
    return d, _blob_sha(raw)

def _reindex():
    _idx.clear()
    for eid, e in D["entries"].items():
        for k in e.get("keys", []): _idx[k] = eid

def _merge(remote):
    """union of two cache files; per entry the newest wins, hidden/favourites by timestamp"""
    if not remote: return
    for eid, e in (remote.get("entries") or {}).items():
        mine = D["entries"].get(eid)
        if mine is None or (e.get("t") or 0) > (mine.get("t") or 0):
            if mine: e["keys"] = sorted(set(e.get("keys", [])) | set(mine.get("keys", [])))
            D["entries"][eid] = e
        else:
            mine["keys"] = sorted(set(e.get("keys", [])) | set(mine.get("keys", [])))
            mine["seen"] = max(mine.get("seen") or 0, e.get("seen") or 0)
    for k, v in (remote.get("hidden") or {}).items():
        if k not in D["hidden"] or v[1] > D["hidden"][k][1]: D["hidden"][k] = v
    for u, it in (remote.get("favs") or {}).items():
        if u not in D["favs"] or (it.get("updated") or 0) > (D["favs"][u].get("updated") or 0): D["favs"][u] = it
    cr = D.setdefault("crime", {})
    for k, v in (remote.get("crime") or {}).items():
        if k not in cr or v.get("t", 0) > cr[k].get("t", 0): cr[k] = v
    D["w"] = max(D.get("w") or 0, remote.get("w") or 0)
    _reindex()

def load():
    """once per process, before the first poll"""
    global _loaded, _sha
    if _loaded or not ENABLED: _loaded = True; return
    _loaded = True
    t0 = time.time()
    try:
        d, sha = _fetch()
        with _lock:
            _sha = sha
            if d:
                _merge(d)
        try:
            from . import favs
            if D["favs"]: favs.merge(D["favs"])
        except Exception: pass
        stats.update(loaded=len(D["entries"]), last_error=None)
        _log(f"loaded {len(D['entries'])} entries in {time.time() - t0:.1f}s")
    except Exception as e:
        stats["last_error"] = f"load: {type(e).__name__}: {str(e)[:120]}"
        _log(f"load failed ({stats['last_error']}); continuing without cache")

def _prune():
    cut = time.time() - PRUNE_DAYS * 86400
    for eid in [k for k, e in D["entries"].items() if (e.get("seen") or 0) < cut]: D["entries"].pop(eid, None)
    for k in [k for k, v in D["hidden"].items() if v[1] < cut]: D["hidden"].pop(k, None)
    for k in [k for k, v in D.get("crime", {}).items() if v.get("t", 0) < time.time() - 150 * 86400]: D["crime"].pop(k, None)
    _reindex()

def _write_now(need=True):
    """one PUT (with sha); on a sha conflict refetch + merge + retry"""
    global _sha, _dirty, _new
    if not ENABLED: return False
    with _wlock:
        if need and not _dirty and (time.time() - (D.get("w") or 0)) <= 20 * 3600: return True   # an earlier queued write already covered it
        for attempt in range(4):
            with _lock:
                _prune(); D["w"] = time.time()
                raw = gzip.compress(json.dumps(D, separators=(",", ":")).encode(), 6)
                sha = _sha; n_ent = len(D["entries"])
                snap_new = _new
            body = {"message": f"cache {n_ent} listings", "content": base64.b64encode(raw).decode(), "branch": "main"}
            if sha: body["sha"] = sha
            try:
                r = httpx.put(f"https://api.github.com/repos/{REPO}/contents/{PATH}", headers=_hdr(), json=body, timeout=120)
            except Exception as e:
                stats["last_error"] = f"write: {type(e).__name__}"; _log(stats["last_error"]); return False
            if r.status_code in (200, 201):
                with _lock:
                    _sha = r.json()["content"]["sha"]; _dirty = False; _new = max(0, _new - snap_new)
                stats.update(writes=stats["writes"] + 1, last_write=time.time(), last_error=None, bytes=len(raw))
                _log(f"wrote {n_ent} entries, {len(raw) / 1024:.0f} KB")
                return True
            if r.status_code in (409, 422):      # someone else wrote (or we had no sha): merge and retry
                try:
                    d, s = _fetch()
                    with _lock: _sha = s; _merge(d)
                    continue
                except Exception as e:
                    stats["last_error"] = f"merge: {type(e).__name__}"; return False
            stats["last_error"] = f"write HTTP {r.status_code}"; _log(stats["last_error"]); return False
    return False

def flush(wait=False, force=False):
    """write if there is anything new (background thread unless wait)"""
    global _last_try
    if not ENABLED: return
    with _lock:
        stale = (time.time() - (D.get("w") or 0)) > 20 * 3600
        if not (_dirty or force or stale): return
    def run(): _write_now(not force)
    if wait: run()
    else: threading.Thread(target=run, daemon=True).start()

def _soon():
    """debounced write for small changes (hidden / favourites)"""
    global _timer, _dirty
    _dirty = True
    if not ENABLED or _timer is not None: return
    def go():
        global _timer
        _timer = None; _write_now()
    _timer = threading.Timer(MIN_GAP, go); _timer.daemon = True; _timer.start()

# ---------------- keys / hashes ----------------
def keys_for(lid):
    return [f"{r['source']}:{r['source_id']}" for r in db.q("SELECT source,source_id FROM sightings WHERE listing_id=?", (lid,))]

def _find(keys):
    for k in keys:
        eid = _idx.get(k)
        if eid and eid in D["entries"]: return eid, D["entries"][eid]
    return None, None

def media_hash(l, max_photos=12):
    s = "|".join((l.get("floorplans") or [])[:3]) + "#" + "|".join((l.get("images") or [])[:max_photos])
    return hashlib.sha1(s.encode()).hexdigest()[:12]

DETAIL_FIELDS = ("title", "address", "postcode", "lat", "lon", "bedrooms", "bathrooms", "property_type", "tenure", "description",
                 "features", "images", "floorplans", "listed_at", "agent", "extra")

# ---------------- ingest-time reuse of the detail page ----------------
def apply_detail(n):
    """If this listing's detail page is cached (and fresh) fill it into the normalised search result and mark it detail_done."""
    if not ENABLED or n.get("detail_done"): return n
    try:
        with _lock:
            eid, e = _find([f"{n['source']}:{n['source_id']}"])
            if not e or not e.get("d") or time.time() - (e.get("dt") or 0) > DETAIL_TTL: return n
            e["seen"] = time.time()
            d = e["d"]
        for k in DETAIL_FIELDS:
            v = d.get(k)
            if v in (None, "", [], {}): continue
            if k in ("images", "floorplans", "features"): n[k] = list(v)
            elif k == "extra": n[k] = {**(n.get(k) or {}), **v}
            elif k == "description":
                if len(v) > len(n.get(k) or ""): n[k] = v
            elif n.get(k) in (None, "", 0): n[k] = v
        n["detail_done"] = True
        stats["hits_detail"] += 1
    except Exception as ex:
        _log(f"apply_detail failed: {ex}")
    return n

def apply_hidden(lid):
    if not ENABLED: return
    try:
        ks = keys_for(lid)
        with _lock:
            hid = any(D["hidden"].get(k, [0, 0])[0] for k in ks)
        if hid: db.x("UPDATE listings SET hidden=1 WHERE id=?", (lid,))
    except Exception: pass

# ---------------- analysis ----------------
def restore_analysis(lid, l, sig):
    """-> analysis dict (also written to the db + emb.npy) if a valid cached one exists, else None"""
    if not ENABLED: return None
    try:
        with _lock:
            eid, e = _find(keys_for(lid))
            if not e or not e.get("a") or e.get("h") != media_hash(l): stats["misses_analysis"] += 1; return None
            an = json.loads(json.dumps(e["a"])); emb = e.get("emb"); e["seen"] = time.time()
        if an.get("embed_backend", "").startswith("colour"): stats["misses_analysis"] += 1; return None
        an["sig"] = sig; an["updated"] = time.time(); an["from_cache"] = True
        d = DATA / "images" / str(lid); d.mkdir(parents=True, exist_ok=True)
        if emb:
            a = np.frombuffer(base64.b64decode(emb["b"]), dtype=np.int8).reshape(emb["n"], -1).astype(np.float32)
            a /= np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-6)
            np.save(d / "emb.npy", a)
        db.x("INSERT OR REPLACE INTO analysis(listing_id,data,updated) VALUES(?,?,?)", (lid, json.dumps(an), time.time()))
        stats["hits_analysis"] += 1
        return an
    except Exception as ex:
        _log(f"restore failed: {ex}"); return None

def _complete(an, l):
    if an.get("errors"): return False
    if (l.get("floorplans") and not an.get("floorplan_files")): return False
    if (l.get("images") and not an.get("n_photos") and "n_photos" in an): return False
    return True

def record(lid, analysed=False):
    """snapshot one listing (detail, routes, analysis, photo vectors) into the in-memory cache"""
    global _new, _dirty
    if not ENABLED: return
    try:
        row = db.q1("SELECT * FROM listings WHERE id=?", (lid,))
        if not row: return
        l = dict(row)
        for k in ("features", "images", "floorplans", "extra"):
            l[k] = json.loads(l[k]) if isinstance(l.get(k), str) else (l.get(k) or ([] if k != "extra" else {}))
        keys = keys_for(lid)
        if not keys: return
        a = db.q1("SELECT data FROM analysis WHERE listing_id=?", (lid,))
        an = json.loads(a["data"]) if a else None
        if not (l.get("detail_done") or an): return
        det = {k: l.get(k) for k in DETAIL_FIELDS}
        rts = {r["anchor"]: [round(r["straight_km"], 3), round(r["drive_min"], 1), round(r["drive_km"], 2), r["method"]] for r in db.q("SELECT * FROM routes WHERE listing_id=?", (lid,))}
        with _lock:
            eid, e = _find(keys)
            if e is None:
                eid = keys[0]; e = D["entries"][eid] = {"keys": [], "t": 0}
            if eid in D["entries"]:
                e["keys"] = sorted(set(e["keys"]) | set(keys))
            for k in keys: _idx[k] = eid
            changed = e.get("d") != det or e.get("r", {}).get("rt") != rts or e.get("r", {}).get("ll") != [l["lat"], l["lon"]]
            if changed:
                e["d"] = det
                e["r"] = {"ll": [l["lat"], l["lon"]], "rt": rts}
                if l.get("detail_done") or not e.get("dt"): e["dt"] = time.time()
            e["seen"] = time.time()
            if an and _complete(an, l):
                h = media_hash(l)
                if analysed or e.get("h") != h or not e.get("a"):
                    keep = {k: v for k, v in an.items() if k not in ("from_cache",)}
                    e["a"] = keep; e["h"] = h
                    f = DATA / "images" / str(lid) / "emb.npy"
                    if f.exists():
                        arr = np.load(f)
                        e["emb"] = {"n": int(arr.shape[0]), "b": base64.b64encode(np.clip(np.round(arr * 127), -127, 127).astype(np.int8).tobytes()).decode()}
                    else: e.pop("emb", None)
                    changed = True
            if changed:
                e["t"] = time.time(); _dirty = True
                if analysed: _new += 1
        if analysed and _new >= BATCH and time.time() - _last_write() > MIN_GAP: flush()
    except Exception as ex:
        _log(f"record failed: {type(ex).__name__}: {ex}")

def _last_write(): return stats["last_write"] or 0

def record_all():
    """end of poll: refresh every listing's snapshot, then write if anything changed"""
    if not ENABLED: return
    for r in db.q("SELECT id FROM listings WHERE active=1"): record(r["id"])
    flush()

def cached_route(l, anchor):
    """-> {straight_km, drive_min, drive_km, method} from the cache, or None"""
    if not ENABLED: return None
    try:
        with _lock:
            eid, e = _find(keys_for(l["id"]))
            if not e or not e.get("r"): return None
            ll = e["r"].get("ll"); row = (e["r"].get("rt") or {}).get(anchor["name"])
        if not ll or not row or row[3] == "estimate": return None
        if abs(ll[0] - l["lat"]) > 1e-4 or abs(ll[1] - l["lon"]) > 1e-4: return None
        return {"straight_km": row[0], "drive_min": row[1], "drive_km": row[2], "method": row[3]}
    except Exception: return None

# ---------------- hidden / favourites ----------------
def set_hidden(lid):
    if not ENABLED: return
    try:
        h = db.q1("SELECT hidden FROM listings WHERE id=?", (lid,))["hidden"]
        with _lock:
            for k in keys_for(lid): D["hidden"][k] = [int(h), time.time()]
        _soon()
    except Exception: pass

def set_favs(items):
    if not ENABLED: return
    with _lock:
        new = json.dumps(items, sort_keys=True)
        if new == json.dumps(D["favs"], sort_keys=True): return
        D["favs"] = json.loads(new)
    _soon()

def status():
    return {**stats, "entries": len(D["entries"])}

def touch():
    """mark the cache as changed (crime lookups etc.) so the next write includes it"""
    global _dirty
    _dirty = True
