"""Property condition: 'needs work' (hard exclude), 'some updating' (penalty) and 'move-in ready' (boost) from the listing text,
plus an optional 'dated interior' score from the photo vectors we already have (CLIP zero-shot, no extra model run)."""
import re
from pathlib import Path
import numpy as np

_S = r"(?:some |a bit of |a little |further |general |full |complete |total |significant |substantial |extensive |major |considerable |thorough |comprehensive |a degree of |cosmetic |internal |external )*"
_WORK = r"(?:modernis|moderniz|updat|refurb|renovat|refresh|improv|redecorat|repair|restor|refit|re-?fit|rewir|re-?wir)\w*"
STRONG = [   # (regex, label) - clear 'needs work' signals
    (r"\b(?:in\s+need\s+of|needs|needing|requires?|requiring|require)\s+" + _S + r"(?:a\s+)?(?:" + _WORK + r"|work\b|attention\b|tlc\b|tender\s+loving\s+care|upgrad\w*)", "needs work / modernisation"),
    (r"\b(?:in\s+need\s+of|needs|requires?)\s+(?:a\s+)?(?:new|complete|full)\s+(?:roof|kitchen|bathroom|boiler|heating|windows|wiring)", "needs major replacement"),
    (r"\b(?:renovation|refurbishment|restoration|improvement|modernisation|modernization)\s+(?:project|opportunity)\b|\b(?:ideal|great|fantastic|superb|excellent|perfect)\s+(?:renovation\s+)?project\b|\bproject\s+(?:property|house|home)\b|\bfixer[- ]upper\b", "renovation project"),
    (r"\bscope\s+(?:to|for)\s+(?:improve|improvement|modernis|moderniz|update|updating|refurb|renovat|add\s+value)\w*", "scope to improve"),
    (r"\bput\s+(?:your|their|his|her)\s+own\s+(?:stamp|mark|touch)|\bstamp\s+your\s+own\b", "put your own stamp"),
    (r"\bun-?modernised\b|\bun-?modernized\b|\bun-?renovated\b|\bun-?refurbished\b|\bun-?inhabitable\b|\buninhabitable\b|\bderelict\b|\bdilapidated\b|\bfire[- ]damage|\bgutted\b|\bshell\s+(?:condition|only)\b", "unmodernised / derelict"),
    (r"\bsold\s+as\s+seen\b", "sold as seen"),
    (r"\bcash\s+(?:buyers?|purchasers?|offers?)\s*(?:only)?\b|\bcash\s+only\b|\bnot\s+(?:currently\s+)?(?:mortgageable|suitable\s+for\s+(?:a\s+)?mortgage)|\bun-?mortgageable\b", "cash buyers only"),
    (r"(?<!modern method of )(?<!modern )\bauction\b(?!\s+(?:house|room))", "auction"),
    (r"\bnon[- ]?(?:standard|traditional)\s+construction\b|\bairey\s+(?:house|home|type|construction|property|built)\b|\bno[- ]fines\b|\bbisf\b|\bsteel[- ]frame\s+construction\b|\bprefab\w*", "non-standard construction"),
    (r"\bstructural(?:ly)?\s+(?:issues?|problems?|defects?|damage[d]?|movement|cracks?|cracking|repairs?|concerns?|faults?|failure|instability|unsound|survey\s+(?:is\s+)?(?:recommended|required|advised))\b|\bhad\s+(?:some\s+)?structural\b|\bsubsidence\b|\bunderpinn\w*|\bdamp\s+(?:problem|issue)s?|\brising\s+damp\b|\bjapanese\s+knotweed\b", "structural / damp issues"),
    (r"\bdated\s+(?:throughout|and\s+tired|decor)|\btired\s+(?:throughout|condition|and\s+dated)|\bin\s+(?:a\s+)?(?:poor|tired|dated)\s+(?:state|condition|order)\b|\bneeds?\s+(?:a\s+)?(?:complete|full)\b", "tired / poor condition"),
]
PROBATE = (r"\bprobate\b", r"\b(?:modernis|moderniz|updat|refurb|renovat|dated|tired|improv)\w*")
MILD = [      # penalty, not an exclusion
    (r"\b(?:some|little|slight|light|minor|cosmetic)\s+(?:modernis|moderniz|updat|refurb|renovat|improv|redecorat|decorat)\w*", "some updating"),
    (r"\bwould\s+benefit\s+from\s+\w*\s*(?:some\s+)?(?:" + _WORK + r"|work\b|attention\b|tlc\b)", "would benefit from updating"),
    (r"\bdated\b|\btired\b|\bold[- ]fashioned\b|\bin\s+need\s+of\s+(?:some\s+)?(?:cosmetic|decorat|redecorat)\w*|\btlc\b|\bscope\s+to\s+(?:extend|add|personalise|personalize)\b|\bpotential\s+to\s+(?:improve|update|upgrade|modernis)\w*|\bmodern\s+(?:method\s+of\s+)?auction\b", "dated / some updating"),
]
GOOD = [
    (r"\b(?:recently|newly|fully|completely|freshly|totally|extensively|tastefully|stylishly|beautifully)\s+(?:been\s+)?(?:renovated|refurbished|modernised|modernized|updated|redecorated|re-?fitted|re-?furbished|re-?decorated|re-?wired|installed|fitted|built|upgraded|remodelled)", "recently renovated"),
    (r"\b(?:renovated|refurbished|modernised|modernized)\s+(?:to|throughout|and\s+extended)\b|\bhas\s+been\s+(?:totally|fully|completely|extensively|recently)\s+\w+\b", "renovated"),
    (r"\bnew\s+(?:kitchen|bathroom|boiler|windows|carpets|roof|flooring|heating|en-?suite)\b|\bnewly\s+fitted\b", "new kitchen/bathroom"),
    (r"\bmove[- ]in\s+(?:ready|condition)\b|\bready\s+to\s+move\s+(?:in|into)\b|\bturn[- ]?key\b|\bready\s+to\s+move\b", "move-in ready"),
    (r"\bimmaculate\b|\bshow\s+(?:home|house)\b|\bexceptionally\s+well[- ]presented\b|\bbeautifully\s+presented\b|\bsuperbly\s+presented\b|\bpresented\s+to\s+(?:a\s+)?(?:an?\s+)?(?:exceptional|very\s+high|high|superb)\s+standard\b|\bfinished\s+to\s+(?:a\s+)?(?:an?\s+)?(?:exceptional|very\s+high|high|superb|exacting)\s+standard\b", "immaculate / show home"),
    (r"\b(?:brand[- ]new|new[- ]?build|newly[- ]built|new\s+home|new\s+development|plot\s+\d+|nhbc|10[- ]year\s+(?:warranty|guarantee)|help\s+to\s+buy|stamp\s+duty\s+paid)\b", "new build"),
]
_c = {}
def _cc(lst):
    k = id(lst)
    if k not in _c: _c[k] = [(re.compile(r, re.I), lab) for r, lab in lst]
    return _c[k]

def _snippet(t, m):
    return re.sub(r"\s+", " ", t[max(0, m.start() - 20): m.end() + 25]).strip()

def text_condition(text, address=""):
    """-> {"state": needs_work|some_updating|ready|unknown, "bad": [...], "mild": [...], "good": [...], "quote": str}"""
    t = re.sub(r"\s+", " ", (text or "").replace("\u2019", "'"))
    out = {"state": "unknown", "bad": [], "mild": [], "good": [], "quote": None}
    for rx, lab in _cc(STRONG):
        m = rx.search(t)
        if m:
            q = m.group(0).strip()
            # "needs work" needs a qualifier-free hit to count when the same sentence says it is only cosmetic
            if lab.startswith("needs work") and re.search(r"\b(?:cosmetic|decorat|minor|slight|light|a\s+little|some)\b", q, re.I) and not re.search(r"\b(?:complete|full|total|significant|substantial|extensive|major)\b", q, re.I):
                out["mild"].append("some updating"); continue
            out["bad"].append(lab); out["quote"] = out["quote"] or _snippet(t, m)
    if re.search(PROBATE[0], t, re.I) and re.search(PROBATE[1], t, re.I):
        out["bad"].append("probate sale needing updating"); out["quote"] = out["quote"] or "probate + modernisation wording"
    for rx, lab in _cc(MILD):
        if rx.search(t) and lab not in out["mild"]: out["mild"].append(lab)
    for rx, lab in _cc(GOOD):
        if rx.search(t) or rx.search(address or ""):
            if lab not in out["good"]: out["good"].append(lab)
    out["state"] = "needs_work" if out["bad"] else ("some_updating" if out["mild"] and not out["good"] else "ready" if out["good"] else "unknown")
    return out

# ---------------- photos: CLIP zero-shot on the stored image vectors ----------------
_P = None
def _prompts():
    global _P
    if _P is None:
        z = np.load(Path(__file__).with_name("condition_prompts.npz"))
        names = ["kit_dated", "kit_modern", "bath_dated", "bath_modern", "kit_any", "bath_any"]
        _P = np.stack([z[n].astype(np.float32) for n in names] + [r.astype(np.float32) for r in z["other"]])   # rows: 6 kitchen/bath classes + the rest
    return _P

def photo_condition(embs):
    """embs: list of L2-normalised CLIP image vectors (the first few photos of a listing).
    A photo counts only if it is clearly a kitchen or bathroom (generic kitchen/bathroom prompts beat all other rooms and exteriors);
    then 'dated' vs 'modern' prompts are compared.  -> {"dated": 0..1, "n": photos used} or None when no kitchen/bathroom is shown."""
    if not embs: return None
    try:
        E = np.stack([np.asarray(e, dtype=np.float32) for e in embs]); P = _prompts()
        if E.shape[1] != P.shape[1]: return None
        def sm(M, rows):
            lg = 100.0 * E @ M[rows].T; lg -= lg.max(1, keepdims=True); p = np.exp(lg); return p / p.sum(1, keepdims=True)
        idx = [4, 5] + list(range(6, len(P)))
        g = sm(P, idx)                                  # generic kitchen / bathroom vs everything else
        isroom = g[:, 0] + g[:, 1] > 0.6
        if not isroom.any(): return None
        out = []
        for i in np.where(isroom)[0]:
            kit = g[i, 0] >= g[i, 1]
            rows = [0, 1] if kit else [2, 3]
            lg = 100.0 * E[i] @ P[rows].T; lg -= lg.max(); p = np.exp(lg); p /= p.sum()
            out.append(float(p[0]))
        return {"dated": round(float(np.mean(out)), 2), "n": len(out)}
    except Exception:
        return None
