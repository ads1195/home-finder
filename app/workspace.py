"""Working-from-home space (a ranking boost, never a filter).

Two people need closable private rooms for simultaneous (confidential) calls, so more rooms is better:
  tier A  5+ bedrooms AND a study / office / garden room or outbuilding          (biggest boost)
  tier B  5+ bedrooms, OR 4 bedrooms plus a study / office / outbuilding
  tier C  a plain 4-bedroom home                                                  (no boost)
plus a small extra when the office is on a different floor or at the other end from the kitchen / living area
(labelled "likely" where only the listing text says so).  Pure function: used by matcher.evaluate()."""
import re

PTS = {"A": 4.0, "B": 2.5, "C": 0.0}
SEP_PTS, SEP_PTS_LIKELY = 1.0, 0.7

_STUDY = re.compile(r"\b(?:study|home[- ]office|work[- ]?room|workspace|office)\b", re.I)
_NOT_STUDY = re.compile(r"post office|office hours|estate office|sales office|office block|head office|office park|(?:from|to) the office", re.I)
_OUTB = re.compile(r"\b(?:garden\s+(?:room|office|studio|cabin)|outbuilding|out[- ]building|summer\s?house|log\s+cabin|(?:detached|separate)\s+(?:office|studio|annexe?)|"
                   r"(?:office|studio|gym)\s+(?:in|at|within|to)\s+the\s+(?:garden|rear)|garden\s+studio|annexe?\b|home\s+office\s+(?:in|at|within)\s+the\s+garden)", re.I)
_UPPER = re.compile(r"(?:(?:first|1st|second|2nd|top|upper)\s+floor|upstairs|loft)[^.]{0,40}\b(?:study|office|work\s?room)\b|\b(?:study|office|work\s?room)\b[^.]{0,40}(?:(?:first|1st|second|2nd|top|upper)\s+floor|upstairs)", re.I)

def _fp_study(fp):
    out = []
    for r in (fp or {}).get("rooms") or []:
        lab = r.get("label") or ""
        if r.get("type") == "study" or (r.get("type") == "conservatory" and re.search(r"garden\s*room|studio|office", lab, re.I)): out.append(r)
    return out

def _txt_has(rx, text, bad=None):
    for m in rx.finditer(text or ""):
        ctx = text[max(0, m.start() - 20): m.end() + 20]
        if bad is not None and bad.search(ctx): continue
        return True
    return False

def assess(beds, text, fp):
    """-> {"tier","pts","label","likely","separate","study","outbuilding","why"}"""
    text = text or ""
    b = beds or 4
    rooms = _fp_study(fp)
    study_plan = bool(rooms)
    study_text = _txt_has(_STUDY, text, _NOT_STUDY)
    outb = _txt_has(_OUTB, text)
    has_study = study_plan or study_text
    big = b >= 5
    if big and (has_study or outb): tier = "A"
    elif big or (b >= 4 and (has_study or outb)): tier = "B"
    else: tier = "C"
    # a closable room away from the kitchen / living space
    sep, likely = None, False
    if study_plan:
        kit = [r for r in (fp or {}).get("rooms") or [] if r.get("type") in ("kitchen", "kitchen_diner", "kitchen_family", "family_kitchen") and r.get("floor") in (None, "ground", "unknown")]
        for r in rooms:
            if r.get("floor") in ("first", "second", "loft"): sep = "on a different floor"; break
            for k in kit:
                if None not in (r.get("x"), r.get("y"), k.get("x"), k.get("y")) and r.get("sheet") == k.get("sheet") \
                        and ((r["x"] - k["x"]) ** 2 + (r["y"] - k["y"]) ** 2) ** 0.5 > 0.42:
                    sep = "at the other end of the house"; break
            if sep: break
    if not sep and outb: sep, likely = "outbuilding / garden room", True
    if not sep and study_text and _UPPER.search(text): sep, likely = "on a different floor", True
    pts = PTS[tier] + ((SEP_PTS_LIKELY if likely else SEP_PTS) if sep else 0.0)
    kind = "study/office" if has_study else ""
    if outb: kind = (kind + " + " if kind else "") + "garden room/outbuilding"
    label = f"{b} bed" + (f" + {kind}" if kind else "")
    basis = "floorplan" if study_plan else "text" if (study_text or outb) else None
    return {"tier": tier, "pts": round(pts, 1), "label": label, "beds": b, "study": bool(has_study), "outbuilding": bool(outb),
            "likely": basis == "text", "separate": sep, "basis": basis,
            "why": ("plan shows a study/office" if basis == "floorplan" else "listing mentions a study/office/outbuilding" if basis else "bedrooms only") + (f"; {sep}" if sep else "")}
