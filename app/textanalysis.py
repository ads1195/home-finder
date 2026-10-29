"""Tri-state checklist from listing text.  state: confirmed | not_mentioned | contradicted
Each result carries `evidence` (snippet + where it came from) shown in the UI."""
import re

def _find(rx, text):
    m = re.search(rx, text, re.I | re.S)
    if not m: return None
    s = max(0, m.start() - 50); e = min(len(text), m.end() + 50)
    return "…" + re.sub(r"\s+", " ", text[s:e]).strip() + "…"

FAMILY = r"(family|snug|play ?room|playroom|day room|sitting area|living area|living space|lounge area|garden room|conservatory|orangery|sun ?room|reception)"
RX = {
 "kitchen_family": {
   "yes": [
     r"kitchen[ /\-]*(?:and |& )?(?:dining[ /\-]*)?(?:diner[ /\-]*)?(?:family|living|snug|play ?room)\s*(?:room|area|space)?",
     r"(?:family|living|day)\s*(?:room)?[ /\-]*(?:dining )?kitchen",
     r"open[ \-]plan\s+(?:\w+\s+){0,3}(?:kitchen|living|family|dining).{0,120}(?:family|living|snug|play|garden room|conservatory|sun ?room|orangery|lounge)",
     r"kitchen.{0,120}(?:open[ \-]plan|opens?|leads?|flows?|access).{0,60}(?:to |into |onto )?(?:a |the )?(?:\w+ ){0,2}(?:family room|snug|play ?room|garden room|conservatory|orangery|sun ?room|day room|living area|lounge)",
     r"(?:family room|snug|play ?room|garden room|conservatory|orangery|sun ?room|day room).{0,120}(?:off|adjoin\w*|open[ \-]plan|leads? (?:from|off)|from).{0,40}kitchen",
     r"kitchen\s*/\s*diner\s*/\s*(?:family|living|snug|play)", r"(?:large|spacious|open[ \-]plan) living kitchen",
     r"kitchen.{0,60}(?:with|and) (?:an? )?(?:adjoining|separate|adjacent) (?:family|snug|play|living)"],
   "no": [r"no (?:separate )?(?:family|snug|play) ?(?:room|area)"]},
 "wc_or_ensuite": {
   "yes": [r"(?:downstairs|ground[ -]floor|guest|cloaks?|cloakroom)[ /]*(?:w\.?c\.?|toilet|cloakroom|cloaks)", r"\bcloakroom\b", r"\bcloaks\b", r"\bw/?c\b.{0,40}(?:ground|hall)", r"ground floor w\.?c",
           r"en[- ]?suite", r"\bensuite\b", r"(?:master|main|principal).{0,60}shower room"],
   "no": [r"no (?:downstairs|ground[ -]floor) (?:w\.?c\.?|toilet|cloakroom)"]},
 "utility": {
   "yes": [r"\butility\b", r"laundry room", r"washing machine (?:and|&) tumble dryer plumbing"],
   "no": [r"no utility"]},
 "parking": {
   "yes": [r"drive[ ]?way", r"\bdrive\b(?! time)", r"off[ -]?(?:street|road) (?:parking|park)", r"garage (?:and|with|&) (?:a )?(?:private )?drive", r"parking for (?:\d|two|three|four|several)",
           r"(?:private|own|gated|block[ -]paved|tarmac|gravel(?:led)?) (?:drive|parking)", r"car port|carport", r"integral garage|detached garage|double garage|single garage|attached garage|\bgarage\b"],
   "no": [r"on[ -]?street parking(?: only| is available| available)?", r"street parking only", r"no (?:off[ -]street )?(?:parking|driveway)", r"(?:permit|resident)s? parking"]},
 "not_overlooked": {
   "yes": [r"not overlooked", r"no overlooking", r"un-?overlooked", r"secluded", r"(?:private|very private) (?:rear )?(?:garden|plot|position)", r"backs? (?:on)?to (?:open )?(?:fields|countryside|farmland|woodland|green|open land|playing fields|paddock)",
           r"(?:open|uninterrupted|rural|far[- ]reaching|field|countryside) views?", r"overlooking (?:open )?(?:fields|farmland|countryside|woodland)", r"corner (?:plot|position)|end (?:plot|position)",
           r"enjoys? (?:a )?(?:rural|open) (?:aspect|position|outlook)", r"cul[- ]de[- ]sac"],
   "no": [r"(?<!not )(?<!un)overlooked by", r"(?<!not )(?<!un)overlooked\b(?! (?:by )?(?:fields|farm|countr|wood))(?<!not overlooked)"]},
 "garden_size": {
   "yes": [r"(?:large|generous|good[- ]?siz(?:e)?d|sizeable|substantial|extensive|spacious|mature|wrap[- ]?around|landscaped|well[- ]?(?:stocked|tended)|south[- ]?(?:west[- ]?)?facing|south[- ]?(?:east[- ]?)?facing).{0,40}(?:garden|plot|grounds|gardens)",
           r"(?:garden|gardens|plot).{0,40}(?:south[- ]?(?:west[- ]?)?facing|large|generous)", r"(?:\d+(?:\.\d+)?|half an?) acre", r"gardens? to (?:three|3|all) sides"],
   "no": [r"small (?:rear |front )?(?:garden|yard)", r"low[- ]maintenance (?:rear )?garden", r"courtyard (?:garden)?", r"no (?:private )?garden", r"yard only"]},
}

def evaluate_text(text, extra_yes=None):
    """text: description + features + floorplan-derived text; returns {key: {state, evidence}}"""
    out = {}
    text = text or ""
    for key, rx in RX.items():
        yes = None
        for pat in rx["yes"]:
            ev = _find(pat, text)
            if ev: yes = ev; break
        no = None
        for pat in rx["no"]:
            ev = _find(pat, text)
            if ev: no = ev; break
        out[key] = {"state": "not_mentioned", "evidence": None}
        if key == "parking":
            # garage alone is weak: only count as parking if a drive is also confirmed OR strong phrase hit
            strong = any(re.search(p, text, re.I) for p in RX["parking"]["yes"][:6])
            if yes and not strong:
                out[key] = {"state": "confirmed", "evidence": yes + "  (garage mentioned; drive not confirmed)", "weak": True}
                continue
        if yes and not (no and key in ("parking", "garden_size", "not_overlooked") and not strong_override(key, yes)):
            out[key] = {"state": "confirmed", "evidence": yes}
        elif no:
            out[key] = {"state": "contradicted", "evidence": no}
    return out

def strong_override(key, yes_ev):
    return False
