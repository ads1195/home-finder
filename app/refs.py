"""Reference homes: houses liked (photos / floorplans / listing URLs) + 'Current home' baseline.
Items can be uploaded in the UI, pasted as listing URLs, or dropped into data/references/ (scanned each poll)."""
import hashlib, json, re, shutil, time
from pathlib import Path
import numpy as np
from . import db, embed, layout, http
from .config import DATA
from .matcher import CURRENT_HOME_DEFAULT

REF_DIR = DATA / "references"
UPLOADS = REF_DIR / "uploads"
SKIP_DIRS = {"current-home-candidates", "current-home"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

def sha_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for ch in iter(lambda: f.read(1 << 20), b""): h.update(ch)
    return h.hexdigest()

def ensure_baseline():
    if not db.q1("SELECT id FROM refs WHERE kind='current_home'"):
        db.x("INSERT INTO refs(kind,label,sha,features,created,notes) VALUES('current_home','Current home (baseline)','current_home',?,?,?)",
             (json.dumps(CURRENT_HOME_DEFAULT), time.time(), "Baseline for 'vs current home'. Edit sq ft when known."))

def current_home():
    ensure_baseline()
    r = db.q1("SELECT features FROM refs WHERE kind='current_home'")
    return json.loads(r["features"])

def update_current_home(feats):
    ensure_baseline()
    cur = current_home(); cur.update(feats)
    db.x("UPDATE refs SET features=? WHERE kind='current_home'", (json.dumps(cur),))
    return cur

def add_image(path, label=None, kind_hint=None, notes=""):
    path = Path(path)
    sha = sha_of(path)
    ex = db.q1("SELECT id FROM refs WHERE sha=?", (sha,))
    if ex: return ex["id"]
    kind = kind_hint
    feats, emb = {}, None
    is_fp = kind == "floorplan" or bool(re.search(r"floor|plan", path.name, re.I))
    if not is_fp and kind is None and layout.ocr_available():
        try:
            r = layout.analyse_floorplan(path)
            if r["usable"] and r["n_labels"] >= 6: is_fp = True
        except Exception: pass
    if is_fp:
        kind = "floorplan"
        try:
            r = layout.analyse_floorplan(path); r.pop("ocr_text", None)
            feats = {"layout": r}
        except Exception as e:
            feats = {"error": str(e)}
    else:
        kind = "photo"
        v = embed.embed_image(path)
        emb = embed.to_blob(v) if v is not None else None
    cur = db.x("INSERT INTO refs(kind,label,path,sha,features,embedding,created,notes) VALUES(?,?,?,?,?,?,?,?)",
               (kind, label or path.stem, str(path), sha, json.dumps(feats), emb, time.time(), notes))
    return cur.lastrowid

def add_url(url, label=None):
    from .sources import make_source
    from .sources.agent import parse_detail_html
    from .sources.base import blank
    from .textanalysis import evaluate_text
    from .matcher import listing_text
    sha = hashlib.sha1(url.encode()).hexdigest()
    ex = db.q1("SELECT id FROM refs WHERE sha=?", (sha,))
    if ex: return ex["id"]
    l = None
    try:
        if "rightmove.co.uk/properties/" in url:
            pid = re.search(r"/properties/(\d+)", url).group(1)
            s = make_source({"type": "rightmove", "id": "rightmove"}); l = blank("rightmove", pid, url); l["extra"] = {}; l = s.fetch_detail(l)
        elif "onthemarket.com/details/" in url:
            pid = re.search(r"/details/(\d+)", url).group(1)
            s = make_source({"type": "onthemarket", "id": "onthemarket"}); l = blank("onthemarket", pid, url); l["extra"] = {}; l = s.fetch_detail(l)
        elif "purplebricks.co.uk/property-for-sale/" in url:
            pid = re.search(r"(\d+)/?$", url).group(1)
            s = make_source({"type": "purplebricks", "id": "purplebricks"}); l = blank("purplebricks", pid, url); l = s.fetch_detail(l)
        else:
            r = http.get(url); l = blank("url", sha[:8], url); l = parse_detail_html(l, r.text, str(r.url))
    except Exception as e:
        raise RuntimeError(f"could not read listing: {e}")
    ck = evaluate_text(listing_text(l))
    img_dir = REF_DIR / "uploads" / f"url_{sha[:10]}"; img_dir.mkdir(parents=True, exist_ok=True)
    embs, fpres = [], []
    for i, u in enumerate((l["images"] or [])[:8]):
        p = download(u, img_dir / f"photo{i}.jpg")
        if p:
            v = embed.embed_image(p)
            if v is not None: embs.append(v)
    for i, u in enumerate((l["floorplans"] or [])[:2]):
        p = download(u, img_dir / f"floorplan{i}.jpg")
        if p and layout.ocr_available():
            try: fpres.append(layout.analyse_floorplan(p))
            except Exception: pass
    fp = layout.combine(fpres) if fpres else None
    if fp: fp.pop("ocr_text", None)
    feats = {"url": url, "title": l["title"], "address": l["address"], "price": l["price"], "bedrooms": l["bedrooms"],
             "checklist": {k: v["state"] for k, v in ck.items()}, "layout": fp, "n_photos": len(embs)}
    emb = embed.to_blob(np.mean(embs, axis=0)) if embs else None
    cur = db.x("INSERT INTO refs(kind,label,url,sha,features,embedding,created) VALUES('listing',?,?,?,?,?,?)",
               (label or l["address"] or url, url, sha, json.dumps(feats), emb, time.time()))
    return cur.lastrowid

def download(url, dest, delay=0.25):
    from .engine_io import fetch_file
    return fetch_file(url, dest, delay)

def scan_folder():
    """Ingest new files/URL stubs dropped into data/references/ (excluding candidate folder)."""
    REF_DIR.mkdir(exist_ok=True); n = 0
    for p in REF_DIR.rglob("*"):
        if not p.is_file(): continue
        rel = p.relative_to(REF_DIR)
        if rel.parts[0] in SKIP_DIRS or p.name.startswith("."): continue
        if rel.parts[0] == "uploads" and rel.parts[1:2] and rel.parts[1].startswith("url_"): continue
        try:
            if p.suffix.lower() in IMG_EXT:
                before = db.q1("SELECT COUNT(*) c FROM refs")["c"]
                add_image(p, label=p.stem); n += db.q1("SELECT COUNT(*) c FROM refs")["c"] - before
            elif p.suffix.lower() in (".url", ".txt") and p.stat().st_size < 4000:
                m = re.search(r"https?://\S+", p.read_text(errors="ignore"))
                if m:
                    before = db.q1("SELECT COUNT(*) c FROM refs")["c"]
                    add_url(m.group(0), label=p.stem); n += db.q1("SELECT COUNT(*) c FROM refs")["c"] - before
        except Exception:
            continue
    return n

def list_refs():
    out = []
    for r in db.q("SELECT id,kind,label,path,url,features,created,notes, embedding IS NOT NULL AS has_emb FROM refs ORDER BY id"):
        r["features"] = json.loads(r["features"] or "{}")
        r["thumb"] = f"/media/refs/{Path(r['path']).relative_to(REF_DIR).as_posix()}" if r["path"] and Path(r["path"]).exists() else None
        out.append(r)
    return out

def delete_ref(rid):
    r = db.q1("SELECT * FROM refs WHERE id=?", (rid,))
    if not r or r["kind"] == "current_home": return False
    db.x("DELETE FROM refs WHERE id=?", (rid,))
    if r["path"] and str(UPLOADS) in r["path"]:
        try: Path(r["path"]).unlink()
        except Exception: pass
    return True

def reference_set():
    photos, layouts = [], []
    for r in db.q("SELECT kind,features,embedding FROM refs WHERE kind!='current_home'"):
        f = json.loads(r["features"] or "{}")
        if r["embedding"]: photos.append(embed.from_blob(r["embedding"]))
        lay = f.get("layout")
        flags = {}
        if r["kind"] == "floorplan" and lay and lay.get("flags"):
            fl = lay["flags"]
            flags = {"kitchen_family": bool(fl.get("kitchen_family")), "utility": bool(fl.get("utility")),
                     "wc_or_ensuite": bool(fl.get("ground_wc") or fl.get("ensuite")), "parking": bool(fl.get("garage"))}
        elif r["kind"] == "listing":
            ck = f.get("checklist") or {}
            flags = {k: v == "confirmed" for k, v in ck.items() if k in ("kitchen_family", "utility", "wc_or_ensuite", "parking")}
        if any(flags.values()): layouts.append(flags)
    from . import private
    ie = private.ideal_embeddings()
    if ie:
        photos = list(photos) + list(ie)
        layouts.append({"kitchen_family": True, "utility": True, "wc_or_ensuite": True, "parking": True})
    return photos, layouts

def similarity(listing_embs, listing_ck, refset):
    photos, layouts = refset
    if not photos and not layouts: return None
    parts, detail = [], {}
    if photos and listing_embs:
        from . import private
        groups = [(g["vecs"], g["sim_range"]) for g in private.ideal_embedding_groups()]
        n_priv = sum(len(g[0]) for g in groups)
        own = photos[: len(photos) - n_priv] if n_priv else photos      # reference_set() appends the private vectors last
        if own: groups.append((own, groups[0][1] if groups else [0.55, 0.85]))
        ss, ms = [], []
        for vecs, (lo, hi) in groups:      # each reference home is scored on its own, against its own calibrated range
            m = float(np.mean([max(embed.cos(p, e) for e in listing_embs) for p in vecs]))
            ms.append(m); ss.append(min(1, max(0, (m - lo) / max(0.01, hi - lo))))
        # best-matching reference counts most; resembling every reference adds a little
        s = ss[0] if len(ss) == 1 else 0.75 * max(ss) + 0.25 * float(np.mean(ss))
        parts.append(("photos", s, 0.6)); detail["photo_cos_mean"] = round(max(ms), 3)
        if len(ss) > 1: detail["photo_by_ref"] = [round(x, 2) for x in ss]
    if layouts:
        vals = []
        for fl in layouts:
            want = [k for k, v in fl.items() if v]
            vals.append(sum(1 for k in want if listing_ck.get(k, {}).get("state") == "confirmed") / len(want))
        parts.append(("layout", float(np.mean(vals)), 0.4)); detail["layout_match"] = round(float(np.mean(vals)), 2)
    if not parts: return None
    w = sum(p[2] for p in parts)
    score = sum(p[1] * p[2] for p in parts) / w
    return {"score": round(score, 3), **detail, "n_ref_photos": len(photos), "n_ref_layouts": len(layouts)}
