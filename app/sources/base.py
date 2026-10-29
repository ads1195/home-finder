import html, re
from ..http import Blocked  # re-export

def strip_html(s):
    if not s: return ""
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", re.sub(r"[ \t\u00a0]+", " ", s)).strip()

def parse_price(s):
    if s is None: return None
    if isinstance(s, (int, float)): return int(s)
    m = re.search(r"£\s*([\d,]+)", str(s)) or re.search(r"([\d,]{5,})", str(s))
    return int(m.group(1).replace(",", "")) if m else None

def blank(source, source_id, url):
    return {"source": source, "source_id": str(source_id), "url": url, "title": "", "address": "",
            "postcode": None, "lat": None, "lon": None, "price": None, "price_text": "",
            "bedrooms": None, "bathrooms": None, "property_type": "", "tenure": "",
            "description": "", "features": [], "images": [], "floorplans": [], "agent": "",
            "status_text": "", "listed_at": None, "extra": {}, "detail_done": False}

class Source:
    """Common adapter interface.  search() returns summary listings (dicts, see blank());
    fetch_detail(l) fills in description/photos/floorplans/coords and sets detail_done."""
    type = "base"
    def __init__(self, cfg):
        self.cfg = cfg
        self.warnings = []
    def search(self, criteria, poll):  # -> list[dict]
        raise NotImplementedError
    def fetch_detail(self, listing):   # -> dict (updated)
        return listing

def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
