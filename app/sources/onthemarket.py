import json, re
from .. import http
from .base import Source, blank, strip_html, parse_price, slugify

BASE = "https://www.onthemarket.com"

def _state(html_):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_, re.S)
    if not m: raise RuntimeError("OnTheMarket: __NEXT_DATA__ not found (layout changed?)")
    return json.loads(m.group(1))["props"]["pageProps"]["initialReduxState"]

class OnTheMarket(Source):
    type = "onthemarket"

    def search(self, criteria, poll):
        out, seen = [], set()
        for name in criteria["search_locations"]:
            for page in range(1, poll["max_pages_per_search"] + 1):
                params = {"radius": "3.0", "min-bedrooms": criteria["bedrooms_min"],
                          "min-price": int(criteria["price_min"] * 0.9), "max-price": int(criteria["price_stretch_max"]),
                          "sort-field": "update_date", "page": page}
                try:
                    r = http.get(f"{BASE}/for-sale/property/{slugify(name)}/", params=params)
                except http.Blocked:
                    raise
                except RuntimeError as e:
                    if "HTTP 404" in str(e): break
                    raise
                res = _state(r.text)["results"]
                lst = res.get("list", [])
                for p in lst:
                    if p["id"] in seen: continue
                    seen.add(p["id"]); out.append(self._summary(p))
                if len(lst) < 25: break
        return out

    def _summary(self, p):
        l = blank("onthemarket", p["id"], f"{BASE}{p['details-url']}")
        loc = p.get("location") or {}
        l.update(title=p.get("property-title", ""), address=p.get("address", ""), lat=loc.get("lat"), lon=loc.get("lon"),
                 price=parse_price(p.get("price")), price_text=" ".join(x for x in [p.get("price-qualifier"), p.get("price")] if x),
                 bedrooms=p.get("bedrooms"), bathrooms=p.get("bathrooms"), property_type=p.get("humanised-property-type", ""),
                 features=p.get("features", []), images=[i["default"] for i in p.get("images", [])[:3]],
                 agent=(p.get("agent") or {}).get("name", ""), status_text=p.get("main-label", "") if p.get("main-label") not in ("Spotlight Property", None) else "")
        for f in p.get("features", []):
            if f.lower().startswith("tenure:"): l["tenure"] = f.split(":", 1)[1].strip().lower()
        return l

    def fetch_detail(self, l):
        r = http.get(l["url"])
        p = _state(r.text)["property"]
        l["description"] = strip_html(p.get("description", "")) or l["description"]
        l["features"] = [f["feature"] for f in p.get("features", [])] or l["features"]
        l["images"] = [i.get("largeUrl") or i.get("url") for i in p.get("images", [])] or l["images"]
        l["floorplans"] = [f.get("largeUrl") or f.get("original") for f in p.get("floorplans", []) if f.get("largeUrl") or f.get("original")]
        l["floorplans"] += [f if isinstance(f, str) else f.get("url") for f in p.get("externalFloorplans", [])]
        loc = p.get("location") or {}
        if loc.get("lat"): l["lat"], l["lon"] = loc["lat"], loc["lon"]
        for k in p.get("keyInfo", []):
            if k.get("title") == "Tenure" and k.get("value"): l["tenure"] = k["value"].lower()
            if k.get("title") == "Council tax" and k.get("value"): l["extra"]["council_tax"] = k["value"]
        l["extra"]["epc"] = (p.get("epc") or {}).get("rating")
        l["extra"]["agent_phone"] = (p.get("agent") or {}).get("telephone")
        if p.get("station"): l["extra"]["otm_stations"] = p["station"]
        l["detail_done"] = True
        return l
