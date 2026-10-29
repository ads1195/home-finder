"""Floorplan analysis: OCR (RapidOCR, ONNX, fully offline) -> room labels + dimensions -> areas,
floors, adjacency heuristics.  All results include evidence strings for the UI."""
import math, re, threading
from pathlib import Path

_ocr = None
_lock = threading.Lock()

import os
LEAN = os.environ.get("PROPDASH_LEAN") == "1"
TESSDATA = os.environ.get("TESSDATA_PREFIX") or str(Path(os.environ.get("PROPDASH_DATA", ".")) / "tessdata")
OCR_MAX_SIDE = int(os.environ.get("PROPDASH_OCR_SIDE", "1700"))

def ocr_available():
    if LEAN:   # low-memory hosting: Tesseract (tesserocr, ~60 MB) instead of RapidOCR/ONNX (~300 MB peak)
        try:
            import importlib.util
            return importlib.util.find_spec("tesserocr") is not None and (Path(TESSDATA) / "eng.traineddata").exists()
        except Exception:
            return False
    try:
        import rapidocr_onnxruntime  # noqa
        return True
    except Exception:
        return False

def _ocr_tesseract(path):
    """two passes (normal + larger scale) merged: Tesseract is scale-sensitive on tiny floorplan labels"""
    a = _ocr_tesseract_at(path, OCR_MAX_SIDE)
    b = _ocr_tesseract_at(path, int(OCR_MAX_SIDE * 1.45))
    ext = a[:]
    for t in b:
        if not any(abs(t[0] - u[0]) < 12 + 0.5 * max(t[2], u[2]) and abs(t[1] - u[1]) < 12 + 0.5 * max(t[3], u[3]) for u in a):
            ext.append(t)
    return ext

def _ocr_tesseract_at(path, max_side):
    import tesserocr
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("L")
        w, h = im.size; sc = max_side / max(w, h)
        if abs(sc - 1) > 0.05: im = im.resize((max(1, round(w * sc)), max(1, round(h * sc))), Image.LANCZOS)
        sc = im.size[0] / w
        out = []
        with tesserocr.PyTessBaseAPI(path=TESSDATA, lang="eng", psm=tesserocr.PSM.SPARSE_TEXT) as api:
            api.SetImage(im)
            api.Recognize()
            it = api.GetIterator()
            if it is not None:
                level = tesserocr.RIL.TEXTLINE
                while True:
                    text = (it.GetUTF8Text(level) or "").strip()
                    bb = it.BoundingBox(level)
                    if text and bb:
                        l, t, r, b = bb
                        out.append(((l + r) / 2 / sc, (t + b) / 2 / sc, (r - l) / sc, (b - t) / sc, text, it.Confidence(level) / 100.0))
                    if not it.Next(level): break
    return out

def ocr_image(path):
    """returns list of (cx, cy, w, h, text, conf)"""
    global _ocr
    if LEAN:
        with _lock: return _ocr_tesseract(path)
    with _lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR()
        res, _ = _ocr(str(path))
    out = []
    for box, text, conf in (res or []):
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        out.append((sum(xs) / 4, sum(ys) / 4, max(xs) - min(xs), max(ys) - min(ys), text.strip(), float(conf)))
    return out

ROOM_TYPES = [
    ("kitchen_family", r"KITCHEN\W*(?:/|-|&|AND)?\W*(?:DINER\W*)?(?:/|-)?\W*(?:FAMILY|LIVING|SNUG|PLAY)"),
    ("family_kitchen", r"(?:FAMILY|LIVING|DAY)\W*(?:ROOM\W*)?(?:/|-|&|AND)?\W*(?:DINING\W*)?KITCHEN"),
    ("kitchen_diner", r"KITCHEN\W*(?:/|-|&|AND)?\W*(?:DINER|DINING|BREAKFAST|BREAKFASTROOM)"),
    ("kitchen", r"KITCHEN|^KIT\b"),
    ("family", r"FAMILY|SNUG|PLAY|DAYROOM|GAMES|PLAYAREA"),
    ("conservatory", r"CONSERVATORY|ORANGERY|SUNROOM|GARDENROOM"),
    ("utility", r"UTILITY|LAUNDRY"),
    ("ensuite", r"EN\W?SUITE|ENSUITE|SHOWERROOM|SHOWER"),
    ("bathroom", r"BATH"),
    ("wc", r"^W\.?C\.?$|CLOAK|TOILET|^WC\b|DOWNSTAIRSWC|GUESTWC"),
    ("dressing", r"DRESSING|WARDROBE|WALKIN"),
    ("bedroom", r"BED(?:ROOM)?\W*\d?|MASTER|PRINCIPAL"),
    ("study", r"STUDY|OFFICE|WORKSPACE"),
    ("dining", r"DINING"),
    ("living", r"LOUNGE|LIVING|SITTING|RECEPTION|DRAWING"),
    ("garage", r"GARAGE|CARPORT"),
    ("hall", r"HALL|ENTRANCE|PORCH|VESTIBULE|LOBBY"),
    ("landing", r"LANDING|GALLERY"),
    ("storage", r"STORE|STORAGE|CUPBOARD|AIRING|BOILER"),
    ("loft", r"LOFT|ATTIC"),
    ("cellar", r"CELLAR|BASEMENT"),
]
FLOORS = [("ground", r"GROUND\W*FLOOR|^GROUND$|^GF$"), ("first", r"(?:1ST|FIRST)\W*FLOOR|^1ST$|^FIRST$"),
          ("second", r"(?:2ND|SECOND)\W*FLOOR|^2ND$|^SECOND$"), ("basement", r"BASEMENT|LOWERGROUND|CELLAR\W*FLOOR"),
          ("loft", r"LOFT\W*(?:ROOM|FLOOR|CONVERSION)|ATTICFLOOR")]
METRIC = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*m?\s*[x×X*]\s*(\d{1,2}(?:[.,]\d{1,2})?)\s*m", re.I)
METRIC2 = re.compile(r"(\d{1,2}[.,]\d{1,2})\s*[x×X*]\s*(\d{1,2}[.,]\d{1,2})")
IMPER = re.compile(r"(\d{1,2})\s*['’`]\s*(\d{1,2})?\s*[\"”'’]*\s*[x×X*]\s*(\d{1,2})\s*['’`]\s*(\d{1,2})?", re.I)
SQFT = re.compile(r"(\d[\d,]{2,5})\s*sq\.?\s*(?:ft|feet)", re.I)
def pick_sqft(full):
    """Whole-house floor area from OCR text. Floorplans often print per-floor figures AND a total; prefer a
    'gross/total/overall' figure, else the value that equals the sum of the others, else the sum of per-floor figures."""
    ms = [(m, int(m.group(1).replace(",", ""))) for m in SQFT.finditer(full)]
    ms = [(m, v) for m, v in ms if 350 <= v <= 6000]
    if not ms: return None
    for m, v in ms:
        pre = full[max(0, m.start() - 70): m.start()].lower()
        if re.search(r"gross|total|overall", pre): return {"sqft": v, "text": m.group(0), "kind": "total"}
    vals = sorted({v for _, v in ms})
    if len(vals) == 1: return {"sqft": vals[0], "text": ms[0][0].group(0), "kind": "single"}
    top, rest = vals[-1], vals[:-1]
    if len(rest) >= 2 and top >= 0.9 * sum(rest): return {"sqft": top, "text": f"{top} sq ft (largest of {vals})", "kind": "total"}
    return {"sqft": sum(vals), "text": f"sum of per-floor {vals}", "kind": "sum"}

SQM = re.compile(r"(\d{2,4}(?:\.\d+)?)\s*sq\.?\s*m(?:etres?)?\b", re.I)

def _clean(t):
    return re.sub(r"[^A-Z0-9/&\-\.]", "", t.upper().replace(" ", ""))

def classify(text):
    c = _clean(text)
    if len(c) < 2 or re.fullmatch(r"[\d\.\-/]+", c): return None
    for typ, rx in ROOM_TYPES:
        if re.search(rx, c): return typ
    return None

def parse_metric(t):
    m = METRIC.search(t) or METRIC2.search(t)
    if not m: return None
    a, b = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
    if 0.8 <= a <= 15 and 0.8 <= b <= 15: return a, b
    return None

def parse_imperial(t):
    m = IMPER.search(t)
    if not m: return None
    a = int(m.group(1)) * 0.3048 + int(m.group(2) or 0) * 0.0254
    b = int(m.group(3)) * 0.3048 + int(m.group(4) or 0) * 0.0254
    if 0.8 <= a <= 15 and 0.8 <= b <= 15: return round(a, 2), round(b, 2)
    return None

def analyse_tokens(tokens, img_size=None):
    """tokens from ocr_image -> structured result."""
    diag = math.hypot(*(img_size or (1000, 1000)))
    heads, rooms, dims, texts = [], [], [], []
    for (cx, cy, w, h, t, conf) in tokens:
        if conf < 0.45: continue
        texts.append(t)
        c = _clean(t)
        fl = next((f for f, rx in FLOORS if re.search(rx, c)), None)
        if fl and len(c) < 20:
            heads.append((fl, cx, cy, t)); continue
        mt = parse_metric(t)
        if mt: dims.append({"cx": cx, "cy": cy, "m": mt, "text": t, "kind": "m"}); continue
        im = parse_imperial(t)
        if im: dims.append({"cx": cx, "cy": cy, "m": im, "text": t, "kind": "ft"}); continue
        typ = classify(t)
        if typ:
            rooms.append({"type": typ, "label": t, "cx": cx, "cy": cy, "w": w, "h": h, "conf": round(conf, 2)})
    # numbering for bedrooms etc handled by label text
    metric = [d for d in dims if d["kind"] == "m"]
    pool = metric if len(metric) >= max(2, len(rooms) // 2) else dims
    used = set()
    pairs = []
    for ri, r in enumerate(rooms):
        for di, d in enumerate(pool):
            dist = math.hypot(r["cx"] - d["cx"], r["cy"] - d["cy"])
            if dist < 0.16 * diag: pairs.append((dist, ri, di))
    for dist, ri, di in sorted(pairs):
        if di in used or "dim" in rooms[ri]: continue
        used.add(di); rooms[ri]["dim"] = pool[di]
    for r in rooms:
        d = r.pop("dim", None)
        if d:
            r["w_m"], r["h_m"] = d["m"]; r["area_m2"] = round(d["m"][0] * d["m"][1], 1); r["dim_text"] = d["text"]
        # floor
        best, bs = None, 1e18
        for fl, hx, hy, ht in heads:
            dy = r["cy"] - hy
            score = (dy if dy >= -10 else 3000 + abs(dy)) + abs(r["cx"] - hx) * 0.6
            if score < bs: best, bs = fl, score
        r["floor"] = best or "unknown"
    return _summarise(rooms, texts, heads, diag)

def _summarise(rooms, texts, heads, diag):
    full = " ".join(texts)
    types = [r["type"] for r in rooms]
    grd = lambda r: r["floor"] in ("ground", "unknown", "basement")
    ev = []
    def has(t, ground=False): return any(r["type"] == t and (not ground or grd(r)) for r in rooms)
    kfam = has("kitchen_family") or has("family_kitchen")
    adj = False
    kit = [r for r in rooms if r["type"] in ("kitchen", "kitchen_diner", "kitchen_family", "family_kitchen") and grd(r)]
    fam = [r for r in rooms if r["type"] in ("family", "conservatory") and grd(r)]
    for k in kit:
        for f in fam:
            if math.hypot(k["cx"] - f["cx"], k["cy"] - f["cy"]) < 0.25 * diag:
                adj = True; ev.append(f'"{k["label"]}" is next to "{f["label"]}" on the ground floor')
    if kfam: ev.append("Combined kitchen/family label on plan: " + next(r["label"] for r in rooms if r["type"] in ("kitchen_family", "family_kitchen")))
    wc_ground = any(r["type"] == "wc" and grd(r) for r in rooms)
    ensuite = has("ensuite")
    utility = has("utility")
    bedrooms = sum(1 for r in rooms if r["type"] == "bedroom")
    studies = sum(1 for r in rooms if r["type"] == "study")
    receptions = sum(1 for r in rooms if r["type"] in ("living", "dining", "family", "conservatory", "study") and grd(r))
    area = sum(r.get("area_m2", 0) for r in rooms if r["type"] not in ("garage",))
    stated = pick_sqft(full)
    if not stated:
        m = SQM.search(full)
        if m and 35 <= float(m.group(1)) <= 560: stated = {"sqft": int(float(m.group(1)) * 10.7639), "text": m.group(0)}
    n = len(rooms)
    out = {
        "n_labels": n, "n_dims": sum(1 for r in rooms if "area_m2" in r),
        "rooms": [{k: v for k, v in r.items() if k in ("type", "label", "floor", "w_m", "h_m", "area_m2", "dim_text", "conf")} for r in rooms],
        "floors_found": sorted({h[0] for h in heads}),
        "flags": {"kitchen_family": bool(kfam or adj), "kitchen_diner": has("kitchen_diner") or kfam or has("family_kitchen"),
                  "ground_wc": wc_ground, "ensuite": ensuite, "utility": utility, "bedrooms_labelled": bedrooms,
                  "studies": studies, "receptions": receptions, "garage": has("garage"), "dressing": has("dressing")},
        "area_labelled_m2": round(area, 1), "area_labelled_sqft": int(area * 10.7639),
        "stated": stated, "adjacency_evidence": ev,
        "ocr_text": full[:1500],
        "usable": n >= 5,
    }
    return out

def analyse_floorplan(path):
    from PIL import Image
    with Image.open(path) as im:
        size = im.size
    toks = ocr_image(path)
    res = analyse_tokens(toks, size)
    res["file"] = Path(path).name
    return res

def analyse_floorplans(paths):
    """-> list of (result|None, error|None).  In lean mode OCR runs in a short-lived child process so its RAM goes back to the OS."""
    paths = [str(p) for p in paths]
    if not paths: return []
    if not LEAN or os.environ.get("PROPDASH_OCR_WORKER") == "1":
        out = []
        for p in paths:
            try: out.append((analyse_floorplan(p), None))
            except Exception as e: out.append((None, str(e)))
        return out
    import subprocess, sys, tempfile, json
    fd, tmp = tempfile.mkstemp(suffix=".json"); os.close(fd)
    try:
        r = subprocess.run([sys.executable, "-m", "app.ocr_worker", tmp] + paths, capture_output=True, timeout=900,
                           env=dict(os.environ, PROPDASH_OCR_WORKER="1"), cwd=str(Path(__file__).resolve().parent.parent))
        if r.returncode != 0:
            return [(None, "ocr worker failed: " + (r.stderr or b"")[-200:].decode("utf8", "replace"))] * len(paths)
        data = json.load(open(tmp))
        return [(d.get("res"), d.get("err")) for d in data]
    except Exception as e:
        return [(None, f"{type(e).__name__}: {e}")] * len(paths)
    finally:
        try: os.unlink(tmp)
        except Exception: pass

def combine(results):
    """merge multiple floorplan results (several sheets) into one summary"""
    if not results: return None
    out = {"sheets": len(results), "rooms": [], "flags": {}, "adjacency_evidence": [], "stated": None, "usable": any(r["usable"] for r in results), "n_labels": 0,
           "area_labelled_m2": 0, "files": [r.get("file") for r in results]}
    for r in results:
        out["rooms"] += r["rooms"]; out["adjacency_evidence"] += r["adjacency_evidence"]; out["n_labels"] += r["n_labels"]
        out["area_labelled_m2"] += r["area_labelled_m2"]
        out["stated"] = out["stated"] or r["stated"]
        for k, v in r["flags"].items():
            out["flags"][k] = (out["flags"].get(k, 0) + v) if isinstance(v, (int, float)) and not isinstance(v, bool) else (out["flags"].get(k, False) or v)
    out["area_labelled_sqft"] = int(out["area_labelled_m2"] * 10.7639)
    return out

def floorplan_text(res):
    """Plain-language lines (fed to the text checklist as extra evidence)."""
    if not res or not res.get("usable"): return ""
    f = res["flags"]; parts = []
    labs = " ; ".join(sorted({r["label"] for r in res["rooms"]}))
    parts.append("Floorplan rooms: " + labs)
    if f.get("kitchen_family"): parts.append("floorplan shows kitchen with adjoining family/snug room")
    if f.get("ground_wc"): parts.append("floorplan shows ground floor WC")
    if f.get("ensuite"): parts.append("floorplan shows en-suite")
    if f.get("utility"): parts.append("floorplan shows utility room")
    return ". ".join(parts)
