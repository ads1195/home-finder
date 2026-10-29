import json, os, secrets, shutil, threading, time, hashlib
from pathlib import Path
from fastapi import FastAPI, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from . import db, engine, geo, refs, push, embed, vision, layout, favs, cache, http as phttp
from .config import DATA, ROOT, default_config
from .sources.agent import detect_platform
from .notify import dispatch
from .matcher import CURRENT_HOME_DEFAULT

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Property Dashboard")
AUTH_FILE = DATA / "auth.json"

def auth_conf():
    mode = os.environ.get("PROPDASH_AUTH", "on").lower()
    if mode == "off": return None
    pw = os.environ.get("PROPDASH_PASSWORD")
    if pw: return pw
    if AUTH_FILE.exists(): return json.loads(AUTH_FILE.read_text())["password"]
    pw = secrets.token_urlsafe(9)
    AUTH_FILE.write_text(json.dumps({"password": pw})); 
    try: AUTH_FILE.chmod(0o600)
    except Exception: pass
    return pw

PASSWORD = None
PUBLIC = ("/login", "/manifest.webmanifest", "/sw.js", "/icons/", "/static/", "/healthz")

@app.on_event("startup")
def _startup():
    global PASSWORD
    db.init(); refs.ensure_baseline()
    PASSWORD = auth_conf()
    engine.start_scheduler()

@app.middleware("http")
async def guard(request: Request, call_next):
    p = request.url.path
    if PASSWORD is None or any(p.startswith(x) for x in PUBLIC):
        return await call_next(request)
    tok = request.cookies.get("pd_session")
    if tok and db.q1("SELECT token FROM sessions WHERE token=?", (tok,)):
        return await call_next(request)
    if p.startswith("/api") or p.startswith("/media"):
        return JSONResponse({"error": "auth required"}, status_code=401)
    return HTMLResponse((STATIC / "login.html").read_text(), status_code=200)

_fails = {}   # client ip -> [timestamps of failed logins]
def _client_ip(request):
    return request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")

@app.post("/login")
async def login(request: Request):
    import asyncio
    ip = _client_ip(request); now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 600]; _fails[ip] = recent
    if len(recent) >= 8:   # brute-force guard: 8 failures per 10 minutes per client
        return JSONResponse({"ok": False, "error": "too many attempts, try again in a few minutes"}, status_code=429)
    form = await request.form()
    if PASSWORD is not None and secrets.compare_digest(str(form.get("password", "")), PASSWORD):
        tok = secrets.token_urlsafe(32)
        db.x("INSERT INTO sessions(token,created) VALUES(?,?)", (tok, time.time()))
        r = JSONResponse({"ok": True})
        https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        r.set_cookie("pd_session", tok, max_age=60 * 86400, httponly=True, samesite="lax", secure=https)  # Secure flag when served over HTTPS (tunnel)
        _fails.pop(ip, None)
        return r
    _fails.setdefault(ip, []).append(now)
    await asyncio.sleep(1)
    return JSONResponse({"ok": False, "error": "wrong password"}, status_code=401)

@app.post("/api/logout")
def logout(request: Request):
    tok = request.cookies.get("pd_session")
    if tok: db.x("DELETE FROM sessions WHERE token=?", (tok,))
    r = JSONResponse({"ok": True}); r.delete_cookie("pd_session"); return r

@app.get("/healthz")
def healthz(): return {"ok": True}

# ------------------------------------------------------------------ listings
def media_url(lid, fname): return f"/media/images/{lid}/{fname}"

def local_media(l, an):
    d = DATA / "images" / str(l["id"])
    def pick(files, urls):
        out = []
        for i, f in enumerate(files):
            if (d / f).exists(): out.append(media_url(l["id"], f))
            elif i < len(urls or []): out.append(urls[i])      # restored from the cache: use the original image URL
        return out
    an = an or {}
    return pick(an.get("photo_files", []), an.get("photo_urls")), pick(an.get("floorplan_files", []), an.get("floorplan_urls"))

def card(l, ev, an, srcs, url=None, boost=0):
    ph, _ = local_media(l, an)
    img = (ph[0] if ph else (l["images"][0] if l["images"] else None))
    ck = ev["checklist"] if ev else {}
    return {"id": l["id"], "title": l["title"], "address": l["address"], "price": l["price"], "price_text": l["price_text"],
            "bedrooms": l["bedrooms"], "bathrooms": l["bathrooms"], "property_type": l["property_type"], "image": img,
            "score": round(ev["score"] + boost, 1) if ev else None, "base_score": ev["score"] if ev else None, "fav_boost": boost or 0,
            "url": url, "image_remote": (l["images"][0] if l["images"] else None), "feat": favs.features(l, ev), "band": ev["band"] if ev else None, "passes": bool(ev and ev["passes"]),
            "reasons": ev["reasons"][:4] if ev else [], "agent": l["agent"], "status_text": l["status_text"],
            "first_seen": l["first_seen"], "listed_at": l["listed_at"], "favourite": l["favourite"], "hidden": l["hidden"],
            "share": favs.share_info(ev), "sources": srcs, "drive": {k.split(" - ")[0][:30]: round(v["drive_min"]) for k, v in (ev["drive"].items() if ev else [])},
            "drive_full": {k: {"min": round(v["drive_min"]), "km": round(v["straight_km"], 1), "est": v.get("method") == "estimate"} for k, v in (ev["drive"].items() if ev else [])},
            "checklist": {k: {"state": v["state"], "label": v["label"], "mode": v["mode"]} for k, v in ck.items()},
            "unconfirmed_must": ev["unconfirmed_must"] if ev else [], "station": ev["station"] if ev else None,
            "similarity": (ev.get("similarity") or {}).get("score") if ev else None,
            "is_new": (time.time() - l["first_seen"]) < 48 * 3600}

@app.get("/api/listings")
def listings(tab: str = "matches", q: str = "", sort: str = "score", hidden: int = 0):
    rows = db.q("""SELECT l.*, e.data AS ev, a.data AS an FROM listings l LEFT JOIN evaluation e ON e.listing_id=l.id
                   LEFT JOIN analysis a ON a.listing_id=l.id WHERE l.active=1 AND l.hidden=?""", (hidden,))
    out = []
    cur = engine.curated()
    try: bst = favs.boosts()
    except Exception: bst = {}
    top = {"matches": set(cur["matches"]), "just_over": set(cur["just_over"])}
    for r in rows:
        if tab == "saved": break
        l = engine._row({k: v for k, v in r.items() if k not in ("ev", "an")})
        ev = json.loads(r["ev"]) if r["ev"] else None
        an = json.loads(r["an"]) if r["an"] else None
        if tab in top and l["id"] not in top[tab]: continue
        if tab == "favourites" and not l["favourite"]: continue
        if tab == "review" and not (ev and not ev["passes"] and ev["unknown_hard"]): continue
        if q and q.lower() not in (l["address"] + " " + l["title"] + " " + (l["description"] or "")).lower(): continue
        sg = db.q("SELECT source,url FROM sightings WHERE listing_id=?", (l["id"],))
        out.append(card(l, ev, an, [x["source"] for x in sg], favs.norm_url(sg[0]["url"]) if sg else None, bst.get(l["id"], 0) if (ev and ev["passes"]) else 0))
    key = {"score": lambda c: -(c["score"] or 0), "price": lambda c: c["price"] or 1e12, "new": lambda c: -(c["first_seen"] or 0)}[sort]
    out.sort(key=key)
    counts = counts_summary()
    tm = db.config()["criteria"].get("top_matches") or {}
    meta = None
    if tab in top:
        jo = tab == "just_over"
        meta = {"shown": len(top[tab]), "passing": cur["passing"][tab], "min_score": tm.get("just_over_min_score" if jo else "min_score"),
                "max_count": tm.get("just_over_max_count" if jo else "max_count")}
    return {"items": out, "counts": counts, "meta": meta}

def counts_summary():
    r = db.q("SELECT e.passes p, e.band b, e.data d FROM listings l JOIN evaluation e ON e.listing_id=l.id WHERE l.active=1 AND l.hidden=0")
    cur = engine.curated()
    m, j = len(cur["matches"]), len(cur["just_over"])
    return {"matches": m, "just_over": j, "show_all": bool(db.config().get("ui", {}).get("show_all_fetched")),
            "passing_hidden": (cur["passing"]["matches"] - m) + (cur["passing"]["just_over"] - j), "all": len(r), "unread": db.q1("SELECT COUNT(*) c FROM notifications WHERE read=0")["c"],
            "review": sum(1 for x in r if (not x["p"]) and json.loads(x["d"]).get("unknown_hard")),
            "favourites": db.q1("SELECT COUNT(*) c FROM listings WHERE favourite=1 AND active=1")["c"]}

@app.get("/api/listings/{lid}")
def listing_detail(lid: int):
    l = engine.get_listing(lid)
    if not l: raise HTTPException(404)
    a = db.q1("SELECT data FROM analysis WHERE listing_id=?", (lid,)); an = json.loads(a["data"]) if a else None
    e = db.q1("SELECT data FROM evaluation WHERE listing_id=?", (lid,)); ev = json.loads(e["data"]) if e else None
    sights = db.q("SELECT source,source_id,url,price,first_seen,last_seen FROM sightings WHERE listing_id=?", (lid,))
    ph, fps = local_media(l, an)
    cfg = db.config()
    if an: an = {k: v for k, v in an.items() if k != "floorplan" or True}
    if an and an.get("floorplan"): an["floorplan"].pop("ocr_text", None)
    return {"listing": l, "analysis": an, "evaluation": ev, "sightings": sights, "photos_local": ph, "floorplans_local": fps,
            "card": card(l, ev, an, [x["source"] for x in sights], favs.norm_url(sights[0]["url"]) if sights else None,
                         (favs.boosts().get(lid, 0) if (ev and ev["passes"]) else 0)),
            "anchors": cfg["criteria"]["anchors"], "current_home": refs.current_home(),
            "notifications": db.q("SELECT kind,created FROM notifications WHERE listing_id=?", (lid,))}

@app.post("/api/listings/{lid}/{action}")
def listing_action(lid: int, action: str):
    if action == "hide": db.x("UPDATE listings SET hidden=1-hidden WHERE id=?", (lid,)); cache.set_hidden(lid)
    elif action == "favourite": db.x("UPDATE listings SET favourite=1-favourite WHERE id=?", (lid,))
    elif action == "reanalyse":
        cfg = db.config(); engine.analyse_listing(lid, cfg, force=True); engine.evaluate_listing(lid, cfg, refs.reference_set() if any(refs.reference_set()) else None)
    else: raise HTTPException(400)
    return {"ok": True}

# ------------------------------------------------------------------ saved homes
@app.post("/api/favourites/sync")
async def favourites_sync(request: Request):
    """Browser localStorage is the master copy; this merges (last-write-wins per url) and returns the merged map."""
    j = await request.json()
    merged = favs.merge(j.get("items") or {})
    cache.set_favs(merged)
    return {"items": merged}

@app.get("/api/favourites/live")
def favourites_live():
    """Fresh cards for saved homes that are still in the current fetch (matched by listing URL)."""
    urls = set(favs.saved_urls()); out = {}
    if not urls: return {"items": {}}
    bst = favs.boosts()
    for r in db.q("SELECT s.url, s.listing_id FROM sightings s JOIN listings l ON l.id=s.listing_id WHERE l.active=1"):
        u = favs.norm_url(r["url"])
        if u not in urls or u in out: continue
        l = engine.get_listing(r["listing_id"]); e = db.q1("SELECT data FROM evaluation WHERE listing_id=?", (l["id"],)); a = db.q1("SELECT data FROM analysis WHERE listing_id=?", (l["id"],))
        ev = json.loads(e["data"]) if e else None
        srcs = [x["source"] for x in db.q("SELECT source FROM sightings WHERE listing_id=?", (l["id"],))]
        out[u] = card(l, ev, json.loads(a["data"]) if a else None, srcs, u, 0)
    return {"items": out}

# ------------------------------------------------------------------ state / poll
def mem_mb():
    try:
        v = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/self/status") if l.startswith(("VmRSS", "VmHWM"))}
        return {"rss": round(v["VmRSS"] / 1024), "peak": round(v["VmHWM"] / 1024)}
    except Exception: return None

@app.get("/api/state")
def get_state():
    cfg = db.config()
    st = {r["source"]: json.loads(r["data"]) for r in db.q("SELECT * FROM source_status")}
    srcs = []
    for s in cfg["sources"]:
        d = st.get(s["id"], {"state": "never_run"})
        srcs.append({**s, **{k: v for k, v in d.items() if k not in ("name", "type", "url")}})
    return {"poll": {**engine.state, "interval": cfg["poll"]["interval_minutes"], "enabled": cfg["poll"]["enabled"],
                     "last": db.get_setting("last_poll")},
            "sources": srcs, "counts": counts_summary(), "total_listings": db.q1("SELECT COUNT(*) c FROM listings")["c"],
            "embed": embed.status, "ocr": layout.ocr_available(), "vision": vision.available(cfg["vision"]),
            "placeholders": cfg.get("placeholders", []), "time": time.time(), "cache": cache.status(), "mem_mb": mem_mb()}

@app.post("/api/poll")
def poll_now():
    if engine.state["running"]: return {"started": False, "reason": "already running"}
    threading.Thread(target=engine.run_poll, args=("manual",), daemon=True).start()
    return {"started": True}

# ------------------------------------------------------------------ config
@app.get("/api/config")
def get_config(): 
    c = db.config()
    if c["vision"].get("api_key"): c["vision"]["api_key"] = "••••"
    return c

@app.put("/api/config")
async def put_config(request: Request):
    new = await request.json()
    old = db.config()
    if new.get("vision", {}).get("api_key") == "••••": new["vision"]["api_key"] = old["vision"].get("api_key", "")
    db.set_setting("config", new)
    threading.Thread(target=engine.re_evaluate_all, daemon=True).start()
    return {"ok": True}

@app.post("/api/config/reset")
def reset_config():
    db.set_setting("config", default_config()); return {"ok": True}

@app.post("/api/geocode")
async def geocode(request: Request):
    j = await request.json(); g = geo.geocode_any(j["address"])
    if not g: raise HTTPException(404, "address not found")
    return g

@app.post("/api/sources")
async def add_source(request: Request):
    j = await request.json()
    url = j["url"].strip()
    if not url.startswith("http"): url = "https://" + url
    cfg = db.config()
    plat = j.get("type") or "auto"
    note = ""
    if plat == "auto":
        try: plat = detect_platform(url)
        except phttp.Blocked as e: plat, note = "generic", f"site is bot-protected ({e.reason}); it will show as blocked"
        except Exception as e: plat, note = "generic", f"could not fetch: {e}"
    from urllib.parse import urlparse
    host = urlparse(url).netloc.replace("www.", "").split(".")[0]
    sid = j.get("id") or host
    if any(s["id"] == sid for s in cfg["sources"]): sid += "-" + secrets.token_hex(2)
    cfg["sources"].append({"id": sid, "name": j.get("name") or host.title(), "type": plat, "url": url.rstrip("/"), "enabled": True})
    db.set_setting("config", cfg)
    return {"ok": True, "id": sid, "type": plat, "note": note}

@app.delete("/api/sources/{sid}")
def del_source(sid: str):
    cfg = db.config(); cfg["sources"] = [s for s in cfg["sources"] if s["id"] != sid]; db.set_setting("config", cfg); return {"ok": True}

# ------------------------------------------------------------------ notifications
@app.get("/api/notifications")
def notifications(limit: int = 100):
    rows = db.q("""SELECT n.*, l.address, l.price, l.bedrooms FROM notifications n JOIN listings l ON l.id=n.listing_id ORDER BY n.created DESC LIMIT ?""", (limit,))
    for r in rows: r["message"] = json.loads(r["message"])
    return rows

@app.post("/api/notifications/read")
def mark_read():
    db.x("UPDATE notifications SET read=1"); return {"ok": True}

@app.post("/api/notify/test")
def notify_test():
    cfg = db.config()
    n = {"title": "Property Dashboard test", "body": "Notifier test message", "url": "/", "kind": "test", "listing_id": 0, "price": 0, "address": ""}
    res = dispatch(cfg["notifiers"], n, baseline=False)
    try: ps = push.send_all(n)
    except Exception as e: ps = f"error {e}"
    return {"results": res, "web_push_sent": ps}

@app.get("/api/push/key")
def push_key(): return {"key": push.vapid_keys()["public"]}

@app.post("/api/push/subscribe")
async def push_sub(request: Request):
    push.subscribe(await request.json()); return {"ok": True}

@app.post("/api/push/unsubscribe")
async def push_unsub(request: Request):
    push.unsubscribe((await request.json())["endpoint"]); return {"ok": True}

# ------------------------------------------------------------------ references
@app.get("/api/refs")
def get_refs():
    return {"items": refs.list_refs(), "folder": str(refs.REF_DIR), "embed": embed.status, "current_home": refs.current_home(), "ideal": ideal_public()}

@app.post("/api/refs/upload")
async def upload_ref(file: UploadFile = File(...), label: str = Form(""), kind: str = Form("")):
    refs.UPLOADS.mkdir(parents=True, exist_ok=True)
    dest = refs.UPLOADS / f"{int(time.time())}_{Path(file.filename).name}"
    with open(dest, "wb") as f: shutil.copyfileobj(file.file, f)
    rid = refs.add_image(dest, label or Path(file.filename).stem, kind or None)
    threading.Thread(target=engine.re_evaluate_all, daemon=True).start()
    return {"id": rid}

@app.post("/api/refs/url")
async def ref_url(request: Request):
    j = await request.json()
    try: rid = refs.add_url(j["url"].strip(), j.get("label"))
    except Exception as e: raise HTTPException(400, str(e))
    threading.Thread(target=engine.re_evaluate_all, daemon=True).start()
    return {"id": rid}

@app.post("/api/refs/rescan")
def ref_rescan():
    n = refs.scan_folder(); threading.Thread(target=engine.re_evaluate_all, daemon=True).start(); return {"added": n}

@app.delete("/api/refs/{rid}")
def ref_delete(rid: int):
    ok = refs.delete_ref(rid); threading.Thread(target=engine.re_evaluate_all, daemon=True).start(); return {"ok": ok}

@app.put("/api/refs/current-home")
async def put_current_home(request: Request):
    cur = refs.update_current_home(await request.json())
    threading.Thread(target=engine.re_evaluate_all, daemon=True).start(); return cur

# ------------------------------------------------------------------ static
app.mount("/media/images", StaticFiles(directory=DATA / "images"), name="media_images")
app.mount("/media/refs", StaticFiles(directory=refs.REF_DIR), name="media_refs")
app.mount("/icons", StaticFiles(directory=STATIC / "icons"), name="icons")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/sw.js")
def sw(): return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

@app.get("/manifest.webmanifest")
def manifest(): return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

@app.get("/")
def index(): return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


def ideal_public():
    from . import private
    i = dict(private.ideal_home()); e = i.pop("embeddings", None)
    i["photo_refs"] = len(private.ideal_embeddings())
    return i
