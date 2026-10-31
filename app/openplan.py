"""The core requirement: a proper open-plan kitchen / dining / family space - big enough for a sofa + TV area as well as dining,
so a parent can watch the children while cooking.

assess() combines the listing text, the floorplan (room labels + sizes) and optional photo evidence into
  {"state": "confirmed" | "unconfirmed" | "absent", "q": 0..1 quality, "area_m2": float|None, "size": str|None, "basis": str, "why": str}
  confirmed   - evidence of a kitchen/dining area that opens onto living/family space (or a very large kitchen-diner)
  unconfirmed - nothing clear either way (no floorplan, unclear text): a heavy score penalty, not an exclusion
  absent      - clearly not there (separate kitchen and dining room, or a kitchen-diner with room only for a table): excluded
Nothing personal is stored here; the size target is passed in (the blended reference homes, ~44 m2)."""
import re

# text evidence ---------------------------------------------------------------------------------------------------------
_JOIN = r"(?:\s*[/&+\-]\s*|\s+and\s+)"        # a real joiner: no bare space, no comma (so "Kitchen, Living Room" lists do not count)
_LIV = r"(?:(?:living|family|lounge|sitting|entertaining|day)\s*(?:room|area|space|zone))"
_T = r"(?:(?!separate|floor|upstairs|first|bedroom)[^.\"*;|\u2022]){0,40}?"                  # a short stretch of text that does not cross a sentence/quote or say "separate"
_NODIM = r"(?!\s*[-:\u2013]?\s*\d)"                  # not the start of a "Kitchen - 3.2m x 3.0m" room schedule
STRONG = [   # kitchen/dining that is one space with living/family (or opens onto a family/garden room or extension)
    r"\bkitchen" + _JOIN + r"(?:(?:diner|dining(?:\s+room)?)" + _JOIN + r")?" + _LIV + r"\b" + _NODIM,
    r"\b(?:living|family|day)\s*(?:room)?\s*[/&+\-]\s*(?:dining\s*(?:room)?\s*[/&+\-]\s*)?kitchen\b" + _NODIM,
    r"\b(?:living|family|day)\s+(?:dining\s+)?kitchen\b" + _NODIM,
    r"\bopen[ \-]?plan\b" + _T + r"\bkitchen\b" + _T + r"\b" + _LIV,
    r"\bopen[ \-]?plan\b" + _T + r"\b" + _LIV + _T + r"\bkitchen\b",
    r"\bkitchen[^.]{0,90}?\b(?:open[ \-]?plan|opens?|opening|flows?|flowing|leads?|leading|open)\s*(?:out\s+)?(?:(?:in)?to|onto)\b[^.]{0,50}?\b(?:family|living|garden room|conservatory|orangery|sun ?room|snug|day room|lounge|sitting|extension)",
    r"\b(?:family room|living area|living room|garden room|conservatory|orangery|sun ?room|snug|day room|extension)\b[^.]{0,70}?\b(?:open to|opens? (?:(?:in)?to|onto)|opening (?:(?:in)?to|onto)|flows? (?:(?:in)?to|onto)|off the|adjoining the|leads? (?:from|off))\s*(?:the\s+)?(?:\w+\s+){0,2}kitchen",
    r"\bkitchen[^.]{0,40}\bwith\s+(?:an?\s+)?(?:adjoining|adjacent|open)\s+(?:family|living|snug|play|dining)",
]
MEDIUM = [   # open-plan kitchen/dining only: counts when it is big enough
    r"\bopen[ \-]?plan\s+(?:[\w']+\s+){0,2}(?:kitchen|dining|living|family)",
    r"\bkitchen\s*[/\-]?\s*(?:diner|dining(?:\s+room)?)\b",
    r"\b(?:dining|breakfast)\s+kitchen\b",
]
NEG = [
    r"\bseparate\s+(?:kitchen|dining\s+room|dining\s+area)\b",
    r"\bgalley\s+kitchen\b",
    r"\b(?:formal\s+)?dining\s+room\b[^.]{0,60}\band\s+(?:a\s+)?(?:separate\s+)?(?:fitted\s+)?kitchen\b",
    r"\bkitchen\s+(?:and|with)\s+(?:a\s+)?separate\s+(?:dining|breakfast)",
]
# kitchen-diner + an adjoining / open snug, family or sitting area (the second accepted layout; scored a little below a true open-plan family room)
_SN = r"(?:snug|family\s+(?:area|space|zone)|sitting\s+area|day\s*room)"
SNUG = [
    r"\bkitchen\s*[/&+\-]\s*(?:dining(?:\s+room)?|diner|breakfast)\s*[/&+\-]\s*" + _SN,                      # room schedule "Kitchen / Dining / Snug"
    r"\bkitchen\s*[/&+\-]?\s*(?:diner|dining(?:\s+room)?|breakfast)\b[^.]{0,25}?\b(?:with|and|plus|leading\s+(?:in)?to|opening\s+(?:in)?to|open\s+to|incorporating|including|benefiting\s+from)\s+(?:an?\s+)?(?:adjoining\s+|adjacent\s+|open\s+|cosy\s+|additional\s+)?" + _SN,
    r"\b" + _SN + r"\b[^.]{0,60}?\b(?:off|adjoining|adjacent\s+to|next\s+to|open\s+to|opens?\s+(?:(?:in)?to|onto)|leads?\s+(?:off|from|to)|accessed\s+(?:from|via))\b[^.]{0,40}\bkitchen",
    r"\bkitchen\b[^.]{0,40}\b(?:with|has|boasts|featuring|leading\s+to|opening\s+(?:in)?to)\s+(?:an?\s+)?(?:adjoining\s+|adjacent\s+|open\s+|cosy\s+|additional\s+)?" + _SN,
]
_csn = None
def snug_text(text):
    global _csn
    if _csn is None: _csn = [re.compile(r, re.I) for r in SNUG]
    t = re.sub(r"\s+", " ", (text or "").replace("\u2019", "'"))
    for r in _csn:
        m = r.search(t)
        if m and not re.search(r"\bseparate\s+(?:kitchen|snug)\b", m.group(0), re.I): return _snip(t, m)
    return None

_cs = _cm = _cn = None
def _c():
    global _cs, _cm, _cn
    if _cs is None:
        _cs = [re.compile(r, re.I) for r in STRONG]; _cm = [re.compile(r, re.I) for r in MEDIUM]; _cn = [re.compile(r, re.I) for r in NEG]
    return _cs, _cm, _cn

def _snip(t, m):
    return re.sub(r"\s+", " ", t[max(0, m.start() - 30): m.end() + 30]).strip()

def text_evidence(text):
    s, m_, n = _c()
    t = re.sub(r"\s+", " ", (text or "").replace("\u2019", "'"))
    # a bare list of room names ("Kitchen, Living Room") must not count: the separators above exclude commas on purpose
    for r in s:
        m = r.search(t)
        if m: return "strong", _snip(t, m)
    for r in m_:
        m = r.search(t)
        if m: return "medium", _snip(t, m)
    for r in n:
        m = r.search(t)
        if m: return "negative", _snip(t, m)
    return None, None

# floorplan evidence ------------------------------------------------------------------------------------------------------
COMB = ("kitchen_family", "family_kitchen")
def _rooms(fp):
    seen, out = set(), []
    for r in fp.get("rooms") or []:      # the same sheet is often listed twice
        if r.get("floor") not in (None, "ground", "unknown", "basement"): continue
        k = (r["type"], r.get("area_m2"), round(r.get("cx", 0) / 10), round(r.get("cy", 0) / 10))
        if k in seen: continue
        seen.add(k); out.append(r)
    return out

def floorplan_evidence(fp):
    """-> dict(ok, comb_area/has_comb, kd_area/has_kd, adj, areas...)"""
    ok = bool(fp.get("usable")) and fp.get("n_labels", 0) >= 6
    rs = _rooms(fp)
    ar = lambda ts: [r["area_m2"] for r in rs if r["type"] in ts and r.get("area_m2")]
    has = lambda ts: any(r["type"] in ts for r in rs)
    kit, fam = ar(("kitchen",)), ar(("family", "conservatory"))
    big = lambda v: v if v and v >= 8 else None          # a "room" under 8 m2 is an OCR slip (e.g. 4.5 for 14.5), not a size
    return {"ok": ok, "has_comb": has(COMB), "comb": big(max(ar(COMB), default=None)),
            "has_kd": has(("kitchen_diner",)), "kd": big(max(ar(("kitchen_diner",)), default=None)),
            "has_kitchen": has(("kitchen", "kitchen_diner") + COMB),
            "kit": max(kit, default=None), "fam": max(fam, default=None), "dining": max(ar(("dining",)), default=None),
            "living": max(ar(("living",)), default=None),
            "has_fam": has(("family", "conservatory")),
            "adj": bool(fp.get("adjacency_evidence"))}

# thresholds (m2) ---------------------------------------------------------------------------------------------------------
KD_OK = 32.0        # a kitchen-diner this big has room for a sofa area as well as dining
KD_MIN = 24.0       # below this: room for a table only
COMB_MIN = 20.0     # kitchen+living in one labelled room: smallest that is still workable

def _q(a, ideal):
    """quality 0..1 of a known open-plan area: full marks near the reference size, lower for smaller-but-adequate"""
    return max(0.3, min(1.0, (a / ideal) ** 1.3)) if a else 0.7

def assess(text, fp, ideal_m2=44.5, photo=None):
    fp = fp or {}
    tev, quote = text_evidence(text)
    f = floorplan_evidence(fp)
    def out(state, q, area, basis, why, size=None, variant=None):
        return {"state": state, "q": round(q, 3), "area_m2": round(area, 1) if area else None, "basis": basis, "why": why, "variant": variant,
                "size": size or (None if not area else "large" if area >= ideal_m2 * 0.9 else "adequate" if area >= 30 else "small"), "quote": quote}
    # 1) kitchen + living/family explicitly one space
    if f["has_comb"]:
        a = f["comb"]
        if a is None or a >= COMB_MIN:
            return out("confirmed", _q(a, ideal_m2) if a else 0.75, a, "floorplan", "kitchen/living space labelled as one room on the plan" + (f" (~{a:.0f} m\u00b2)" if a else ""))
        return out("absent", 0.0, a, "floorplan", f"kitchen/living room is only ~{a:.0f} m\u00b2 - too small for a sofa area")
    # 1b) kitchen-diner + adjoining/open snug (or family / sitting area): accepted, scored a little below a true ~44 m2 open-plan family room
    sq_ = snug_text(text)
    plan_snug = f["has_kd"] and f["adj"] and any(r["type"] == "family" and re.search(r"snug|family|sitting|day\s*room", r.get("label") or "", re.I) and not re.search(r"play|games|study|office", r.get("label") or "", re.I) for r in _rooms(fp))
    sm_ = None
    if tev == "strong":
        t_ = re.sub(r"\s+", " ", (text or "").replace("\u2019", "'"))
        for r_ in _c()[0]:
            m_ = r_.search(t_)
            if m_: sm_ = m_.group(0); break
    strong_true = tev == "strong" and not re.search(r"snug", sm_ or "", re.I)
    if not strong_true and (sq_ or plan_snug or tev == "strong"):
        a = ((f["kd"] or f["kit"] or 0) + (f["fam"] or 0)) or None
        if sq_: quote = sq_
        q = 0.66 + 0.14 * min(1.0, a / ideal_m2) if a else 0.7
        return out("confirmed", min(0.8, q), a, "floorplan" if plan_snug else "text", "kitchen-diner with an adjoining/open snug or family area" + (f" (~{a:.0f} m\u00b2 together)" if a else ""), variant="kd_snug")
    if tev == "strong":
        base = f["kd"] or ((f["kit"] or 0) + ((f["dining"] or 0) if "din" in (quote or "").lower() else 0)) or None
        a = (base or 0) + (f["fam"] or 0) or None
        return out("confirmed", max(0.5, _q(a, ideal_m2)) if a else 0.75, a, "text", "listing describes the kitchen/dining as one space with living/family room" + (f" (~{a:.0f} m\u00b2 on plan)" if a else ""))
    # 2) a kitchen-diner on the plan (or described as open-plan kitchen/dining)
    if f["has_kd"] or tev == "medium":
        a = f["kd"]
        if a is None and f["kit"] and f["dining"] and tev == "medium": a = f["kit"] + f["dining"]
        if a and a >= KD_OK:
            return out("confirmed", _q(a, ideal_m2), a, "floorplan", f"large kitchen-diner (~{a:.0f} m\u00b2) with room for a sofa area")
        if a and a < KD_MIN and f["ok"]:
            return out("absent", 0.0, a, "floorplan", f"kitchen-diner is only ~{a:.0f} m\u00b2 - room for a table but not a sofa area")
        if a: return out("unconfirmed", 0.4, a, "floorplan", f"kitchen-diner ~{a:.0f} m\u00b2 - check there is room for a sofa area")
        return out("unconfirmed", 0.5, None, "text", "described as open-plan kitchen/dining - size not known, check there is space for a sofa area")
    # 3) kitchen next to a family room / conservatory on the plan, but nothing says it is open
    if f["adj"] and f["has_fam"]:
        a = (f["kit"] or 0) + (f["fam"] or 0) or None
        return out("unconfirmed", 0.35, a, "floorplan", "kitchen sits next to a family room/conservatory on the plan - check it is open-plan")
    # 4) a plan with a kitchen but no open-plan sign: separate kitchen (+ dining) room
    if f["ok"] and f["has_kitchen"]:
        bits = "separate kitchen and dining room" if f["dining"] or tev == "negative" else "separate kitchen"
        return out("absent", 0.0, f["kit"], "floorplan", f"{bits} on the plan, no open-plan family space")
    if tev == "negative":
        return out("absent", 0.0, None, "text", "listing describes a separate kitchen / dining room")
    if photo and photo >= 0.5:
        return out("unconfirmed", 0.4, None, "photos", "no floorplan/clear text; photos look open-plan - check")
    return out("unconfirmed", 0.2, None, "none", "no floorplan or clear description of the kitchen/living space")
