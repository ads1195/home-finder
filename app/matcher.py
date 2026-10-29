"""Hard filters + tri-state checklist + scoring.  Pure functions (no I/O) so they are easy to test."""
import re
from .geo import haversine_km
from .private import ideal_home as _ideal_home
from .private import current_home_default as _chd; CURRENT_HOME_DEFAULT = _chd()


def listing_text(l):
    parts = [l.get("title") or "", l.get("address") or "", " ".join(l.get("features") or []), l.get("description") or ""]
    ex = l.get("extra") or {}
    def camel(s): return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(s)) if s else ""
    for k in ("parking", "garage"):
        v = ex.get(k)
        if v and str(v).lower() not in ("none", "unknown", ""):
            parts.append(f"{camel(v)} {k}.")
    if ex.get("garden") and str(ex["garden"]).lower() not in ("none", ""):
        parts.append(f"{camel(ex['garden'])} garden.")
    return "\n".join(p for p in parts if p)

def ptype_ok(l, c):
    t = ((l.get("property_type") or "") + " " + (l.get("title") or "")).lower()
    for bad in c.get("exclude_property_types", []):
        if bad.lower() in t: return False, f"type '{bad}' excluded"
    inc = [x.lower() for x in c.get("property_types", [])]
    if inc and t.strip() and not any(x in t for x in inc) and (l.get("property_type") or "").strip():
        return False, f"type '{l.get('property_type')}' not in allowed list"
    return True, ""

def evaluate(l, routes, analysis, cfg, refs_sim=None, current_home=None):
    c = cfg["criteria"]
    text = listing_text(l)
    low = text.lower()
    hard, reasons = [], []
    def H(key, ok, detail, status=None):
        hard.append({"key": key, "ok": ok, "detail": detail, "status": status or ("pass" if ok else "fail")})
        if not ok and (status or "fail") == "fail": reasons.append(f"{key}: {detail}")
    # availability
    st = (l.get("status_text") or "").lower()
    avail = not re.search(r"sold|stc|under offer|sale agreed|withdrawn|let agreed", st)
    H("available", avail, l.get("status_text") or "on market")
    # price / band
    p = l.get("price"); band = None
    if p is None: H("price", False, "price unknown", "unknown")
    elif p < c["price_min"]: H("price", False, f"£{p:,} below £{c['price_min']:,}")
    elif p <= c["price_max"]: band = "in_budget"; H("price", True, f"£{p:,} within £{c['price_min']:,}–£{c['price_max']:,}")
    elif p <= c["price_stretch_max"]: band = "just_over"; H("price", True, f"£{p:,} just over budget (stretch ≤ £{c['price_stretch_max']:,})")
    else: H("price", False, f"£{p:,} above stretch ceiling £{c['price_stretch_max']:,}")
    b = l.get("bedrooms")
    if b is None: H("bedrooms", False, "bedrooms unknown", "unknown")
    else: H("bedrooms", b >= c["bedrooms_min"], f"{b} bed (min {c['bedrooms_min']})")
    if c.get("bathrooms_min") and l.get("bathrooms") is not None:
        H("bathrooms", l["bathrooms"] >= c["bathrooms_min"], f"{l['bathrooms']} bath (min {c['bathrooms_min']})")
    ok, why = ptype_ok(l, c); H("property_type", ok, why or (l.get("property_type") or "unspecified"))
    ten = c.get("tenure", "any")
    if ten != "any" and l.get("tenure"):
        H("tenure", ten in l["tenure"].lower(), l["tenure"])
    miss = [k for k in c.get("must_keywords", []) if k.lower() not in low]
    if c.get("must_keywords"): H("must_keywords", not miss, "missing: " + ", ".join(miss) if miss else "all present")
    bad = [k for k in c.get("exclude_keywords", []) if re.search(r"\b" + re.escape(k.lower()) + r"\b", low)]
    H("exclude_keywords", not bad, "found: " + ", ".join(bad) if bad else "none found")
    # area
    sa = c.get("search_area")
    if sa and l.get("lat") is not None:
        d = haversine_km(l["lat"], l["lon"], sa["lat"], sa["lon"]) / 1.609344
        H("search_area", d <= sa["radius_miles"], f"{d:.1f} mi from area centre (max {sa['radius_miles']})")
    # anchors
    drive = {}
    for a in c.get("anchors", []):
        r = routes.get(a["name"])
        if not r:
            H(f"anchor:{a['name']}", False, "no location/route yet", "unknown"); continue
        drive[a["name"]] = r
        ok_s = r["straight_km"] <= a["max_straight_km"]
        ok_d = r["drive_min"] <= a["max_drive_min"]
        est = " (est.)" if r.get("method") == "estimate" else ""
        H(f"anchor:{a['name']}", ok_s and ok_d, f"{r['straight_km']:.1f} km straight, {r['drive_min']:.0f} min drive{est} (max {a['max_drive_min']} min)")
    # checklist (tri-state)
    ck = merge_checklist(analysis, cfg)
    contradicted, unknown = [], []
    for k, item in ck.items():
        if item["mode"] == "must":
            if item["state"] == "contradicted": contradicted.append(k)
            elif item["state"] == "not_mentioned": unknown.append(k)
    if contradicted:
        H("must_haves", False, "contradicted: " + ", ".join(ck[k]["label"] for k in contradicted))
    else:
        hard.append({"key": "must_haves", "ok": True, "status": "pass",
                     "detail": ("none contradicted; unconfirmed: " + ", ".join(ck[k]["label"] for k in unknown)) if unknown else "all confirmed"})
    unknown_hard = any(h["status"] == "unknown" for h in hard)
    passes = all(h["ok"] for h in hard)
    # ---- scoring ----
    w = c["score_weights"]
    parts = {}
    tot_w = 0.0
    anchors = c.get("anchors", [])
    if drive and anchors:
        num = den = 0
        for a in anchors:
            r = drive.get(a["name"])
            if r: num += a["weight"] * max(0.0, 1 - r["drive_min"] / a["max_drive_min"]); den += a["weight"]
        parts["drive"] = (num / den if den else 0) * w["drive"]
    tot_w += w["drive"]
    cw = sum(i["weight"] for i in ck.values() if i["mode"] != "off") or 1
    cs = 0
    for i in ck.values():
        if i["mode"] == "off": continue
        cs += i["weight"] * {"confirmed": 1.0, "not_mentioned": 0.25, "contradicted": 0.0}[i["state"]]
    parts["checklist"] = cs / cw * w["checklist"]; tot_w += w["checklist"]
    ws, hits = 0, []
    for name, (rx, pts) in c.get("workspace_boosts", {}).items():
        if re.search(rx, text + " " + (analysis or {}).get("floorplan_text", ""), re.I):
            ws += pts; hits.append(name)
    parts["workspace"] = min(ws, 12) / 12 * w["workspace"]; tot_w += w["workspace"]
    if refs_sim is not None:
        parts["similarity"] = refs_sim["score"] * w["similarity"]; tot_w += w["similarity"]
    fp = (analysis or {}).get("floorplan") or {}
    lay = 0.0
    sq = fp.get("stated", {}) and fp["stated"].get("sqft") or 0
    if not sq:
        m_ = re.search(r"(\d[\d,]{2,5})\s*(?:sq\.?\s*(?:ft|feet)|square f)", text, re.I)
        if m_: sq = int(m_.group(1).replace(",", ""))
    if not sq: sq = fp.get("area_labelled_sqft") or 0   # sum of labelled rooms: an underestimate, used last
    if sq: lay += min(1.0, sq / 1800) * 0.6
    fl = fp.get("flags") or {}
    if fl.get("receptions", 0) >= 2: lay += 0.25
    if fl.get("studies", 0) >= 1: lay += 0.15
    parts["layout"] = min(lay, 1) * w["layout"]; tot_w += w["layout"]
    # ---- ideal-home benchmark: size curve + feature fit + small bonus for beating it ----
    ideal = _ideal_home()
    sw = w.get("size", 14)
    if sq and sw:
        parts["size"] = size_curve(sq, ideal) * sw; tot_w += sw
    vi = vs_ideal(l, text, ck, fp, fl, sq, b, ideal, analysis, c)
    iw = w.get("ideal", 35)
    if iw: parts["ideal"] = vi["fit"] * iw; tot_w += iw
    bw = w.get("beyond", 3)
    if bw: parts["beyond"] = min(1.0, vi["n_beats"] / 3) * bw; tot_w += bw
    st_ = l.get("_station")
    if st_:
        parts["station"] = max(0.0, 1 - st_["km"] / 5.0) * w["station"]
    tot_w += w["station"]
    parts["price"] = (1.0 if band == "in_budget" else 0.3 if band == "just_over" else 0) * w["price"]; tot_w += w["price"]
    score = round(sum(parts.values()) / tot_w * 100, 1) if tot_w else 0
    # vs current home
    ch = current_home or CURRENT_HOME_DEFAULT
    vs = {"bedrooms": (b, ch.get("bedrooms")), "bathrooms": (l.get("bathrooms"), ch.get("bathrooms")),
          "receptions": (fl.get("receptions") or None, ch.get("receptions")),
          "sqft": (sq or None, ch.get("sqft")), "sqft_basis": ("floorplan/listing" if (fp.get("stated") or {}).get("sqft") else ("estimated from labelled rooms" if sq else None)),
          "garage_or_drive": (ck["parking"]["state"], "confirmed" if ch.get("garage") or ch.get("driveway") else "unknown"),
          "utility": (ck["utility"]["state"], "no" if not ch.get("utility") else "yes"),
          "downstairs_wc_or_ensuite": (ck["wc_or_ensuite"]["state"], "yes")}
    return {"hard": hard, "passes": passes, "band": band, "reasons": reasons, "unknown_hard": unknown_hard,
            "checklist": ck, "contradicted_must": contradicted, "unconfirmed_must": unknown,
            "score": score, "score_parts": {k: round(v, 2) for k, v in parts.items()}, "workspace_hits": hits,
            "drive": drive, "station": st_, "vs_current": vs, "vs_ideal": vi, "similarity": refs_sim}

def merge_checklist(analysis, cfg):
    """Combine text / floorplan / vision evidence into the tri-state checklist."""
    c = cfg["criteria"]; an = analysis or {}
    text_ck = an.get("text") or {}
    fp = an.get("floorplan") or {}
    fl = fp.get("flags") or {}
    vis = an.get("vision_checklist") or {}
    fp_ok = bool(fp.get("usable"))
    out = {}
    for key, spec in c["checklist"].items():
        item = dict(text_ck.get(key) or {"state": "not_mentioned", "evidence": None})
        item["source"] = "text" if item["state"] != "not_mentioned" else None
        # floorplan evidence
        fp_map = {"kitchen_family": fl.get("kitchen_family"), "utility": fl.get("utility"),
                  "wc_or_ensuite": fl.get("ground_wc") or fl.get("ensuite")}
        if fp_ok and fp_map.get(key):
            item = {"state": "confirmed", "source": "floorplan",
                    "evidence": "Floorplan OCR: " + ("; ".join(fp.get("adjacency_evidence") or []) or ", ".join(sorted({r["label"] for r in fp["rooms"]}))[:200])}
        v = vis.get(key)
        if v and item["state"] == "not_mentioned": item = {**v, "source": "vision"}
        elif v and v["state"] == "confirmed" and item["state"] == "contradicted": item = {**v, "source": "vision", "conflict": "text said otherwise"}
        # floorplan 'likely absent'
        item["likely_absent_on_floorplan"] = False
        if fp_ok and item["state"] == "not_mentioned" and fp.get("n_labels", 0) >= 6 and key in ("utility", "kitchen_family") and not fp_map.get(key):
            item["likely_absent_on_floorplan"] = True
            item["note"] = "floorplan analysed: no matching room label found"
            if c.get("floorplan_can_contradict"): item["state"] = "contradicted"; item["evidence"] = item["note"]
        item.update(label=spec["label"], mode=spec["mode"], weight=spec["weight"])
        out[key] = item
    return out


def size_curve(sq, ideal):
    """0..1: mild penalty below the ideal's size, full marks at the ideal, a little extra up to ~'beats' size (never penalises bigger)"""
    tgt = ideal.get("sqft", 1430); lo = ideal.get("size_floor_sqft", 1250); hi = ideal.get("size_beats_sqft", 1600)
    pts = [(900, 0.0), (1100, 0.3), (lo, 0.62), (tgt, 0.88), (hi, 1.0)]
    if sq <= pts[0][0]: return 0.0
    if sq >= pts[-1][0]: return 1.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= sq <= x1: return y0 + (y1 - y0) * (sq - x0) / max(1, x1 - x0)
    return 1.0

_RX = {
    "bifold": r"bi-?fold|bi-?folding|sliding doors?|orangery|rear extension|extended|extension|sky ?lights?|roof lantern|lantern",
    "lounge": r"separate (lounge|living|sitting|reception)|(front|formal) (lounge|living room|sitting room|reception)|(two|2|three|3|multiple) (reception|living|sitting)|second reception|snug",
    "wc": r"(downstairs|ground floor|guest|cloak ?room)\W*(wc|w\.c\.|toilet|cloakroom|cloaks)|cloak ?room|\bw\.?c\b|downstairs (wc|toilet|cloakroom)",
    "ensuite": r"en[- ]?suite",
    "detached": r"\bdetached\b",
}

def vs_ideal(l, text, ck, fp, fl, sq, b, ideal, analysis, c):
    """Compare a listing with the benchmark.  status: beats | meets | short | unknown (+ points 0..1 used for the fit)."""
    low = (text + " " + (analysis or {}).get("floorplan_text", "")).lower()
    fp_ok = bool(fp.get("usable")) and fp.get("n_labels", 0) >= 6
    rows, beats = [], 0
    def row(key, label, want, this, status, weight, pts=None):
        rows.append({"key": key, "label": label, "ideal": want, "this": this, "status": status, "weight": weight, "pts": pts})
    frooms = [r for r in (fp.get("rooms") or []) if r.get("area_m2")]
    def gfl(r): return r.get("floor") in (None, "ground")
    def ckst(k):
        i = ck.get(k) or {}
        return {"confirmed": "meets", "contradicted": "short"}.get(i.get("state"), "unknown")
    # bedrooms
    bi = ideal.get("bedrooms", 4)
    if b is None: row("bedrooms", "Bedrooms", f"{bi}", "?", "unknown", 2)
    else: row("bedrooms", "Bedrooms", f"{bi}", str(b), "beats" if b > bi else "meets" if b == bi else "short", 2)
    # detached
    pt = ((l.get("property_type") or "") + " " + (l.get("title") or "")).lower()
    if "semi" in pt or "terrac" in pt or "town house" in pt or "link" in pt:
        row("detached", "Detached", "yes", (l.get("property_type") or "not detached"), "short", 1.5)
    elif "detached" in pt or re.search(_RX["detached"], low): row("detached", "Detached", "yes", "yes", "meets", 1.5)
    else: row("detached", "Detached", "yes", (l.get("property_type") or "?"), "unknown", 1.5)
    # size
    tgt = ideal.get("sqft", 1430)
    if sq: row("size", "Floor area", f"~{tgt:,} sq ft", f"~{int(sq):,} sq ft", "beats" if sq >= ideal.get("size_beats_sqft", 1600) else "meets" if sq >= tgt * 0.97 else "short", 0)
    else: row("size", "Floor area", f"~{tgt:,} sq ft", "not stated", "unknown", 0)
    # open plan kitchen / family
    row("kitchen_family", "Big open-plan kitchen-diner-family room", "yes (rear extension)", {"meets": "yes", "short": "no", "unknown": "not confirmed"}[ckst("kitchen_family")], ckst("kitchen_family"), 4)
    # size of that open-plan space, from the floorplan (labelled combined room, or kitchen + adjoining family room)
    want_k = ideal.get("open_plan_m2", 44.0)
    comb = [r["area_m2"] for r in frooms if r["type"] in ("kitchen_family", "family_kitchen", "kitchen_diner") and gfl(r)]
    kit = [r["area_m2"] for r in frooms if r["type"] in ("kitchen", "kitchen_diner") and gfl(r)]
    fam = [r["area_m2"] for r in frooms if r["type"] in ("family", "conservatory") and gfl(r)]
    ka = max(comb + ([max(kit) + max(fam)] if kit and fam and fl.get("kitchen_family") else []), default=0)
    if ka:
        ratio = ka / want_k
        row("kitchen_area", "Open-plan kitchen/dining/family size", f"~{want_k:.0f} m\u00b2", f"~{ka:.0f} m\u00b2", "beats" if ratio >= 1.12 else "meets" if ratio >= 0.9 else "short", 3, min(1.0, ratio ** 1.5))
    else: row("kitchen_area", "Open-plan kitchen/dining/family size", f"~{want_k:.0f} m\u00b2", "not measurable", "unknown", 3)
    bf = re.search(_RX["bifold"], low)
    row("bifolds", "Bifolds / extension / skylights", "yes", "mentioned" if bf else "not mentioned", "meets" if bf else "unknown", 0.8)
    # separate lounge
    sep = fl.get("receptions", 0) >= 1 or re.search(_RX["lounge"], low)
    row("lounge", "Separate lounge", "yes", "yes" if sep else "not confirmed", "meets" if sep else ("short" if fp_ok else "unknown"), 3)
    want_l = ideal.get("lounge_m2", 16.0)
    la = max([r["area_m2"] for r in frooms if r["type"] == "living" and gfl(r)], default=0)
    if la: row("lounge_area", "Lounge size", f"~{want_l:.0f} m\u00b2", f"~{la:.0f} m\u00b2", "beats" if la >= want_l * 1.15 else "meets" if la >= want_l * 0.9 else "short", 1.5, min(1.0, (la / want_l) ** 1.5))
    else: row("lounge_area", "Lounge size", f"~{want_l:.0f} m\u00b2", "not measurable", "unknown", 1.5)
    row("utility", "Utility room", "yes", {"meets": "yes", "short": "no", "unknown": "not confirmed"}[ckst("utility")], ckst("utility"), 3)
    wc = fl.get("ground_wc") or re.search(_RX["wc"], low)
    row("wc", "Downstairs WC", "yes", "yes" if wc else "not confirmed", "meets" if wc else ("short" if fp_ok else "unknown"), 2.5)
    es = fl.get("ensuite") or re.search(_RX["ensuite"], low)
    row("ensuite", "En-suite", "yes", "yes" if es else "not confirmed", "meets" if es else ("short" if fp_ok else "unknown"), 2.5)
    ba = l.get("bathrooms")
    if ba is None: row("bathrooms", "Family bathroom (plus en-suite)", "2 bathrooms", "?", "unknown", 1)
    else: row("bathrooms", "Family bathroom (plus en-suite)", f"{ideal.get('bathrooms', 2)} bathrooms", f"{ba}", "beats" if ba > ideal.get("bathrooms", 2) else "meets" if ba >= ideal.get("bathrooms", 2) else "short", 1)
    row("parking", "Garage / driveway at the front", "yes", {"meets": "yes", "short": "no", "unknown": "not confirmed"}[ckst("parking")], ckst("parking"), 1.5)
    gs = ckst("garden_size")
    row("garden", "Decent rear garden", "yes", {"meets": "yes", "short": "no", "unknown": "not confirmed"}[gs], gs, 2)
    if (ck.get("not_overlooked") or {}).get("state") == "confirmed": row("private", "Less overlooked than the ideal", "modest overlooking", "private", "beats", 0)
    if fl.get("studies", 0) >= 1 or re.search(r"\b(study|home office|office)\b", low): row("study", "Study / extra room", "bedroom 4 used as office", "yes", "beats", 0)
    # bedroom sizes (floorplan): compare the four biggest bedrooms with the ideal's four
    want_b = sorted(ideal.get("bedroom_m2") or [11.0, 9.4, 8.5, 5.4], reverse=True)
    bas = sorted([r["area_m2"] for r in frooms if r["type"] == "bedroom"], reverse=True)
    if len(bas) >= 4:
        rs = [min(1.2, bas[i] / want_b[i]) for i in range(4)]; avg = sum(rs) / 4
        row("bed_sizes", "Bedroom sizes (4 biggest)", " / ".join(f"{x:.0f}" for x in want_b) + " m\u00b2", " / ".join(f"{x:.0f}" for x in bas[:4]) + " m\u00b2",
            "beats" if avg >= 1.1 else "meets" if avg >= 0.92 else "short", 2, min(1.0, avg ** 1.5))
    else: row("bed_sizes", "Bedroom sizes (4 biggest)", " / ".join(f"{x:.0f}" for x in want_b) + " m\u00b2", "not measurable", "unknown", 2)
    pts = {"beats": 1.0, "meets": 1.0, "unknown": 0.35, "short": 0.0}
    wsum = sum(r["weight"] for r in rows) or 1
    fit = sum(r["weight"] * (r["pts"] if r.get("pts") is not None else pts[r["status"]]) for r in rows) / wsum
    fp_rows = [r for r in rows if r["key"] in ("kitchen_family", "kitchen_area", "lounge", "lounge_area", "utility", "wc", "ensuite", "bed_sizes")]
    fp_known = sum(1 for r in fp_rows if r["status"] != "unknown")
    n_beats = sum(1 for r in rows if r["status"] == "beats")
    cnt = {s: sum(1 for r in rows if r["status"] == s) for s in ("beats", "meets", "short", "unknown")}
    return {"rows": rows, "fit": round(fit, 3), "n_beats": n_beats, "counts": cnt, "fp_known": fp_known, "fp_total": len(fp_rows), "label": ideal.get("label", "Ideal home"),
            "ideal_sqft": tgt}
