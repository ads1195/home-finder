"""Last-known-results snapshot.  Render's free disk is wiped on every sleep / redeploy, which used to leave the dashboard empty until
the first poll finished (~5 min).  After every COMPLETED poll the scored listings (rows, sightings, routes, analysis, evaluation,
notifications) are written as one gzipped JSON file (snapshot.json.gz) to the same private GitHub repo as the analysis cache, and
at start-up - before the first poll - they are loaded straight back into SQLite with their original ids, so the counts and the
listing views are populated within seconds and ids stay stable across restarts.  Nothing here is written unless CACHE_REPO and
CACHE_TOKEN are configured; any failure just falls back to the old behaviour (an empty start)."""
import os, json, gzip, time, base64
import httpx
from . import db, cache

PATH = os.environ.get("SNAPSHOT_PATH", "snapshot.json.gz")
VER = 1
MAX_AGE = 3 * 86400            # the poll deactivates listings not seen for 3 days; don't restore anything older than that
SHRINK_GUARD = 0.5             # never overwrite a good snapshot with one that has less than half as many listings
TABLES = ("sightings", "routes", "analysis", "evaluation", "notifications")     # all keyed by listing_id
state = {"saved_at": None, "saved_n": 0, "restored_at": None, "restored_n": 0, "restore_seconds": None, "bytes": 0, "error": None}
_sha = None

def _log(m):
    try: cache.logger(f"snapshot: {m}")
    except Exception: print("snapshot:", m)

def _url(): return f"https://api.github.com/repos/{cache.REPO}/contents/{PATH}"

def _get_sha():
    r = httpx.get(_url(), headers=cache._hdr(), timeout=30, follow_redirects=True)
    if r.status_code == 404: return None
    r.raise_for_status()
    return r.json().get("sha")

def _dump(sql, args=()):
    cur = db.conn().execute(sql, args)
    return {"cols": [d[0] for d in cur.description], "rows": [list(r) for r in cur.fetchall()]}

def build():
    """-> snapshot dict of every active listing and what the UI needs for it"""
    lst = _dump("SELECT * FROM listings WHERE active=1")
    ids = {r[lst["cols"].index("id")] for r in lst["rows"]}
    out = {"v": VER, "ts": time.time(), "n": len(ids), "listings": lst, "settings": {"baseline_done": bool(db.get_setting("baseline_done", False))}}
    for t in TABLES:
        d = _dump(f"SELECT * FROM {t}"); i = d["cols"].index("listing_id")
        d["rows"] = [r for r in d["rows"] if r[i] in ids]
        out[t] = d
    return out

def save(sources_ok=1):
    """Call ONLY after a poll has completed.  Returns True if written."""
    global _sha
    if not cache.ENABLED: return False
    try:
        if sources_ok < 1: _log("not written: no source succeeded in this poll"); return False
        snap = build(); n = snap["n"]
        if n == 0 or (state["saved_n"] and n < SHRINK_GUARD * state["saved_n"]):
            _log(f"not written: {n} listings vs {state['saved_n']} in the last snapshot"); return False
        raw = gzip.compress(json.dumps(snap, separators=(",", ":"), default=str).encode(), 6)
        body = {"message": f"snapshot {n} listings", "content": base64.b64encode(raw).decode(), "branch": "main"}
        for attempt in range(3):
            if _sha is None or attempt: _sha = _get_sha()
            if _sha: body["sha"] = _sha
            r = httpx.put(_url(), headers=cache._hdr(), json=body, timeout=120)
            if r.status_code in (200, 201):
                _sha = r.json()["content"]["sha"]
                state.update(saved_at=snap["ts"], saved_n=n, bytes=len(raw), error=None)
                _log(f"wrote {n} listings, {len(raw) / 1024:.0f} KB"); return True
            if r.status_code not in (409, 422): break
            _sha = None
        state["error"] = f"write HTTP {r.status_code}"; _log(state["error"]); return False
    except Exception as e:
        state["error"] = f"write: {type(e).__name__}: {str(e)[:100]}"; _log(state["error"]); return False

def _insert(table, d, keep=None, idcol="listing_id"):
    c = db.conn()
    have = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    idx = [i for i, col in enumerate(d["cols"]) if col in have]
    cols = [d["cols"][i] for i in idx]
    rows = [[r[i] for i in idx] for r in d["rows"] if keep is None or r[d["cols"].index(idcol)] in keep]
    c.executemany(f"INSERT OR REPLACE INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", rows)
    return len(rows)

def restore():
    """Load the snapshot into an EMPTY database.  Returns the snapshot time (epoch) or None."""
    global _sha
    if not cache.ENABLED: return None
    if db.q1("SELECT COUNT(*) c FROM listings")["c"]: return None        # a persistent disk already has data
    t0 = time.time()
    try:
        r = httpx.get(_url(), headers=cache._hdr(True), timeout=60, follow_redirects=True)
        if r.status_code == 404: _log("no snapshot yet"); return None
        r.raise_for_status()
        _sha = cache._blob_sha(r.content)
        snap = json.loads(gzip.decompress(r.content))
        if snap.get("v") != VER: _log("unknown snapshot version"); return None
        L = snap["listings"]; ci = {c: i for i, c in enumerate(L["cols"])}
        cut = time.time() - MAX_AGE
        L["rows"] = [x for x in L["rows"] if (x[ci["last_seen"]] or 0) > cut]
        keep = {x[ci["id"]] for x in L["rows"]}
        if not keep: _log("snapshot has nothing recent enough"); return None
        cache.load()          # also loads the 'Not for us' votes, so downvoted homes are never visible, not even briefly
        c = db.conn()
        _insert("listings", L)
        for t in TABLES: _insert(t, snap[t], keep)
        if snap.get("settings", {}).get("baseline_done"): db.set_setting("baseline_done", True)
        try:
            from . import votes; votes.apply()      # inside the same transaction: downvoted homes are never visible, not even for a moment
        except Exception as e: _log(f"votes apply failed: {e}")
        c.commit()
        state.update(restored_at=snap["ts"], restored_n=len(keep), saved_at=snap["ts"], saved_n=snap.get("n", len(keep)), bytes=len(r.content), error=None)
        _log(f"restored {len(keep)} listings (saved {time.strftime('%H:%M', time.localtime(snap['ts']))}) in {time.time() - t0:.1f}s")
        _after(keep)
        state["restore_seconds"] = round(time.time() - t0, 1)
        return snap["ts"]
    except Exception as e:
        state["error"] = f"restore: {type(e).__name__}: {str(e)[:100]}"; _log(state["error"]); return None

def _after(ids):
    """second step (the listings are already visible): photo vectors and hidden flags from the analysis cache"""
    try:
        cache.load()
        for lid in ids:
            cache.restore_emb(lid)
            h = cache.hidden_state(lid)
            if h is not None: db.x("UPDATE listings SET hidden=? WHERE id=?", (int(h), lid))
    except Exception as e:
        _log(f"post-restore step failed: {type(e).__name__}: {e}")
