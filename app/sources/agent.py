"""Local independent estate-agent adapters.

One generic class covers many agents via platform presets, so adding an agent = adding a URL:
  propertyfile  - 'Property File' CMS sites (/search/N.html, /property-details/ID/...)
  wppf          - WordPress 'WP Property Feed' / Apex27 sites (/property/slug/)       
  propertyhive  - WordPress 'PropertyHive' sites (/property/slug/, ?department=...)   
  generic       - anything else: finds listing links on likely index pages and reads detail pages
                  through schema.org JSON-LD / OpenGraph / embedded coordinates.
Anything behind a bot wall (Cloudflare etc.) is reported as Blocked and left alone."""
import json, re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from .. import http
from .base import Source, blank, strip_html, parse_price, slugify

BAD_IMG = re.compile(r"logo|icon|avatar|sprite|badge|placeholder|pixel|banner|footer|social|flag|\.svg|epc|qr", re.I)
PTYPES = ["end of terrace", "end terrace", "semi-detached", "semi detached", "detached bungalow", "bungalow", "detached", "terraced",
          "town house", "townhouse", "cottage", "farmhouse", "barn", "flat", "apartment", "maisonette", "park home", "land", "house"]

def detect_platform(url):
    """Fetch the home page and sniff the CMS. Returns (platform, note)."""
    r = http.get(url)
    h = r.text.lower()
    if "propertyfile" in h or "/property-details/" in h: return "propertyfile"
    if "wppf" in h: return "wppf"
    if "propertyhive" in h or "property-hive" in h: return "propertyhive"
    return "generic"

def _ld_nodes(soup):
    out = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            j = json.loads(s.string or s.get_text() or "")
        except Exception:
            try: j = json.loads(re.sub(r"[\x00-\x1f]+", " ", s.get_text()))
            except Exception: continue
        stack = [j]
        while stack:
            n = stack.pop()
            if isinstance(n, list): stack.extend(n)
            elif isinstance(n, dict):
                out.append(n)
                for v in n.values():
                    if isinstance(v, (dict, list)): stack.append(v)
    return out

def parse_detail_html(l, html_, base_url):
    soup = BeautifulSoup(html_, "lxml")
    nodes = _ld_nodes(soup)
    og = {m.get("property"): m.get("content") for m in soup.find_all("meta") if m.get("property", "").startswith("og:") and m.get("content")}
    title = (soup.title.get_text(strip=True) if soup.title else "") or og.get("og:title", "")
    price = None
    for n in nodes:
        t = str(n.get("@type"))
        offers = n.get("offers")
        if isinstance(offers, list) and offers: offers = offers[0]
        if isinstance(offers, dict) and offers.get("price"): price = price or parse_price(offers["price"])
        if n.get("price") and not price: price = parse_price(n.get("price"))
        geo = n.get("geo")
        if isinstance(geo, dict) and geo.get("latitude"):
            try: l["lat"], l["lon"] = float(geo["latitude"]), float(geo["longitude"])
            except Exception: pass
        if any(k in t for k in ("RealEstateListing", "Residence", "House", "Product", "Accommodation", "Offer")):
            if n.get("name") and not l["title"]: l["title"] = str(n["name"])
            d = n.get("description")
            if d and len(str(d)) > len(l["description"]): l["description"] = strip_html(str(d))
            a = n.get("address") or n.get("contentLocation", {}).get("address") if isinstance(n.get("contentLocation"), dict) or n.get("address") else None
            if isinstance(a, dict):
                s = ", ".join(str(a.get(k)) for k in ("streetAddress", "addressLocality", "postalCode") if a.get(k))
                if s and not l["address"]: l["address"] = s
    soup_text = soup.get_text("\n")
    if not price:
        for sel in ("[class*=price]", "[id*=price]", "h1", "h2"):
            for el in soup.select(sel)[:6]:
                m = re.search(r"£\s?(\d{2,3},\d{3}(?:,\d{3})?)", el.get_text(" "))
                if m and int(m.group(1).replace(",", "")) >= 30000: price = int(m.group(1).replace(",", "")); break
            if price: break
    if not price:
        m = re.search(r"(?:£|&pound;)\s?(\d{3},\d{3}(?:,\d{3})?)", html_[:60000])
        price = int(m.group(1).replace(",", "")) if m else None
    l["price"] = l["price"] or price
    if price and not l["price_text"]:
        l["price_text"] = f"£{price:,}"
    if not l["title"]: l["title"] = og.get("og:title") or title
    if not l["address"]:
        l["address"] = re.sub(r"\s*[-|–].*$", "", og.get("og:title") or title)
    if not l["lat"]:
        for rx in (r'data-latitude="(-?\d+\.\d+)"[^>]*data-longitude="(-?\d+\.\d+)"', r'"lat(?:itude)?"\s*:\s*"?(-?\d+\.\d+)"?\s*,\s*"(?:lng|lon|long|longitude)"\s*:\s*"?(-?\d+\.\d+)',
                   r'[?&](?:q|ll|center)=(-?5\d\.\d+),\s*(-?\d\.\d+)', r'lat=(-?5\d\.\d+)&(?:amp;)?lng=(-?\d\.\d+)'):
            m = re.search(rx, html_)
            if m:
                try: l["lat"], l["lon"] = float(m.group(1)), float(m.group(2)); break
                except Exception: pass
    bm = re.search(r"(\d+)\s*[- ]?bed", (title + " " + (og.get("og:title") or "") + " " + l["title"]), re.I)
    if bm and not l["bedrooms"]: l["bedrooms"] = int(bm.group(1))
    if not l["bedrooms"]:
        bm = re.search(r"(\d+)\s*(?:double )?bed(?:room)?s?\b", soup_text[:6000], re.I)
        if bm: l["bedrooms"] = int(bm.group(1))
    if not l["property_type"]:
        low = (title + " " + l["title"]).lower()
        for p in PTYPES:
            if p in low: l["property_type"] = p; break
    pcm = re.search(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", l["address"] + " " + (title or ""))
    if pcm and not l["postcode"]: l["postcode"] = f"{pcm.group(1)} {pcm.group(2)}"
    # description fallback
    if len(l["description"]) < 80:
        for sel in ("[class*=description]", "#description", "[itemprop=description]", "article", "main"):
            el = soup.select_one(sel)
            if el and len(el.get_text(strip=True)) > 150:
                l["description"] = strip_html(str(el)); break
        else:
            l["description"] = og.get("og:description", l["description"])
    # features
    feats = []
    for sel in ("[class*=feature] li", "[class*=key-feature] li", "[class*=bullets] li", "ul.features li"):
        feats = [x.get_text(" ", strip=True) for x in soup.select(sel)]
        if feats: break
    if feats: l["features"] = feats[:20]
    # images / floorplans
    imgs, fps = [], []
    for im in soup.find_all("img"):
        src = im.get("data-src") or im.get("data-lazy-src") or im.get("data-full") or im.get("src") or ""
        if not src or src.startswith("data:"): continue
        src = urljoin(base_url, src.split(" ")[0])
        alt = (im.get("alt") or "") + " " + " ".join(im.get("class") or [])
        if re.search(r"floor ?-?plan", src + alt, re.I): fps.append(src); continue
        if BAD_IMG.search(src): continue
        imgs.append(src)
    for a in soup.find_all("a", href=True):
        hrf = a["href"]
        if re.search(r"floor-?plan", hrf, re.I) and re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", hrf, re.I): fps.append(urljoin(base_url, hrf))
        elif re.search(r"\.(jpe?g|webp)(\?|$)", hrf, re.I) and not BAD_IMG.search(hrf) and re.search(r"listing|property|resize|photo|image|media|upload", hrf, re.I): imgs.append(urljoin(base_url, hrf))
    if og.get("og:image"): imgs.insert(0, urljoin(base_url, og["og:image"]))
    l["images"] = list(dict.fromkeys(imgs))[:40] or l["images"]
    l["floorplans"] = list(dict.fromkeys(fps))[:4]
    tm = re.search(r"\b(freehold|leasehold|share of freehold)\b", soup_text, re.I)
    if tm: l["tenure"] = tm.group(1).lower()
    l["detail_done"] = True
    return l

LINK_PATTERNS = {
    "propertyfile": re.compile(r"/property-details/\d+/"),
    "wppf": re.compile(r"/property/[a-z0-9-]+/?$"),
    "propertyhive": re.compile(r"/property/[a-z0-9-]+/?$"),
    "generic": re.compile(r"/(property|properties|property-details|property-for-sale|for-sale|details|buy)/[^/?#]*[a-z0-9-]{6,}[^?#]*$", re.I),
}

class AgentSite(Source):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.type = cfg["type"]
        self.base = cfg["url"].rstrip("/")
        self.sid = cfg["id"]

    def _index_urls(self, criteria, poll):
        t = self.type
        if t == "propertyfile":
            q = {"instruction_type": "Sale", "department": "Residential", "showstc": "off", "bedrooms": criteria["bedrooms_min"],
                 "minprice": int(criteria["price_min"] * 0.9), "maxprice": int(criteria["price_stretch_max"])}
            qs = "&".join(f"{k}={v}" for k, v in q.items())
            return [f"{self.base}/search/{p}.html?{qs}" for p in range(1, poll["max_pages_per_search"] + 1)]
        if t == "wppf":
            return [f"{self.base}/property/?wppf_search=to-buy"] + [f"{self.base}/property/page/{p}/?wppf_search=to-buy" for p in range(2, poll["max_pages_per_search"] + 3)]
        if t == "propertyhive":
            return [f"{self.base}/properties/?department=residential-sales&minimum_bedrooms={criteria['bedrooms_min']}"] + \
                   [f"{self.base}/properties/page/{p}/?department=residential-sales&minimum_bedrooms={criteria['bedrooms_min']}" for p in range(2, poll["max_pages_per_search"] + 1)]
        paths = ["/", "/properties-for-sale/", "/property-for-sale/", "/properties/", "/buy/", "/for-sale/", "/search/?instruction_type=Sale", "/property/"]
        return [self.base + p for p in paths]

    def search(self, criteria, poll):
        pat = LINK_PATTERNS.get(self.type, LINK_PATTERNS["generic"])
        found, host = {}, urlparse(self.base).netloc.replace("www.", "")
        got_any_page = False
        for u in self._index_urls(criteria, poll):
            try:
                r = http.get(u)
            except http.Blocked:
                if not got_any_page: raise
                break
            except RuntimeError as e:
                if self.type in ("generic",) or "HTTP 404" in str(e): continue
                raise
            got_any_page = True
            soup = BeautifulSoup(r.text, "lxml")
            n_before = len(found)
            for a in soup.find_all("a", href=True):
                full = urljoin(str(r.url), a["href"]).split("#")[0]
                p = urlparse(full)
                if p.netloc.replace("www.", "") != host: continue
                if not pat.search(p.path if self.type != "propertyfile" else p.path + "/"): continue
                if re.search(r"/page/\d+|/search/|\.html\?|to-let|lettings", full) and self.type != "propertyfile": continue
                found.setdefault(full, a)
            if self.type != "generic" and len(found) == n_before: break
            if self.type == "generic" and len(found) > 60: break
        out = []
        for url, a in found.items():
            sid = (re.search(r"/property-details/(\d+)/", url) or re.search(r"/([^/]+)/?$", urlparse(url).path)).group(1)
            l = blank(self.sid, sid, url)
            txt = a.get_text(" ", strip=True)
            card = a
            for _ in range(4):
                if card.parent is None: break
                card = card.parent
                if len(card.get_text(" ", strip=True)) > 60 and card.name in ("li", "article", "div") and card.find("img"): break
            ct = card.get_text(" ", strip=True)
            l["price"] = parse_price(re.search(r"£\s?[\d,]{5,}", ct).group(0)) if re.search(r"£\s?[\d,]{5,}", ct) else None
            bm = re.search(r"(\d+)\s*(?:bed|bedroom)", ct, re.I)
            l["bedrooms"] = int(bm.group(1)) if bm else None
            addr = re.sub(r"^\s*(?:for sale|sold stc|under offer|new instruction|reduced)\s*", "", txt, flags=re.I)
            addr = re.split(r"\s+\d+\s*(?:bed|bedroom)s?\b|\s+(?:detached|semi-detached|terraced|bungalow|house|flat|offers|guide price|£)|\s+(?:\d[\d ]* )?\|", addr, maxsplit=1, flags=re.I)[0].strip(" ,-|")
            l["address"] = addr[:120] or url.rsplit("/", 1)[-1].replace("-", " ")
            l["agent"] = self.cfg.get("name", self.sid)
            im = card.find("img")
            if im and (im.get("src") or im.get("data-src")): l["images"] = [urljoin(url, im.get("data-src") or im.get("src"))]
            out.append(l)
        return out

    def fetch_detail(self, l):
        r = http.get(l["url"])
        parse_detail_html(l, r.text, str(r.url))
        if self.type == "propertyfile":
            ids = re.search(r"/property-details/(\d+)/", l["url"]).group(1)
            idx = sorted({int(x) for x in re.findall(rf"/resize/{ids}/(\d+)/1250", r.text)})
            fpn = {int(m) for m in re.findall(rf'/resize/{ids}/(\d+)/\d+[^"]*"[^>]*alt="Floorplan', r.text)}
            l["images"] = [f"{self.base}/resize/{ids}/{i}/1250" for i in idx if i not in fpn] or l["images"]
            l["floorplans"] = [f"{self.base}/resize/{ids}/{i}/1250" for i in sorted(fpn)] or l["floorplans"]
        l["agent"] = self.cfg.get("name", self.sid)
        return l
