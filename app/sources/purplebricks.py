import json, re
from .. import http
from .base import Source, blank, strip_html, slugify

BASE = "https://www.purplebricks.co.uk"

def flight(html_):
    parts = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html_, re.S)
    return "".join(json.loads('"' + p + '"') for p in parts)

def resolve(t, ref):
    """Resolve a '$25' text-chunk reference from the RSC flight stream."""
    if not (isinstance(ref, str) and re.fullmatch(r"\$[0-9a-f]+", ref)): return ref
    m = re.search(r"(?m)(?:^|\n)" + re.escape(ref[1:]) + r":T([0-9a-f]+),", t)
    if not m: return ""
    n = int(m.group(1), 16)
    return t[m.end():].encode()[:n].decode("utf8", "ignore")

class Purplebricks(Source):
    type = "purplebricks"
    STYLES = {1: "Detached", 6: "Terraced", 7: "End of terrace", 8: "Semi-detached", 9: "Detached", 12: "Flat", 27: "Apartment",
              18: "Semi-detached bungalow", 19: "Detached bungalow", 23: "Town house", 11: "Ground floor flat"}

    def search(self, criteria, poll):
        out, seen = [], set()
        for name in criteria["search_locations"][:10]:
            for page in range(1, poll["max_pages_per_search"] + 1):
                params = {"searchRadius": 3, "sortBy": 1, "page": page,
                          "priceFrom": int(criteria["price_min"] * 0.9), "priceTo": int(criteria["price_stretch_max"])}
                try:
                    r = http.get(f"{BASE}/search/property-for-sale/west-yorkshire/leeds/{slugify(name)}", params=params)
                except http.Blocked:
                    raise
                except RuntimeError as e:
                    if "HTTP 404" in str(e): break
                    raise
                t = flight(r.text)
                if '"properties":[' not in t:
                    break
                i = t.index('"properties":[') + len('"properties":')
                arr, _ = json.JSONDecoder().raw_decode(t[i:])
                for p in arr:
                    if p["id"] in seen or p.get("toLet"): continue
                    seen.add(p["id"]); out.append(self._summary(p))
                if len(arr) < 10: break
        return out

    def _summary(self, p):
        l = blank("purplebricks", p["id"], p.get("listingUrl") or f"{BASE}/property-for-sale/{p['id']}")
        f = p.get("propertyFeatures") or {}
        pc = re.search(r"[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}", p.get("address", "") or "")
        l.update(title=p.get("title", ""), address=p.get("address", ""), postcode=p.get("postcode") or (pc.group(0) if pc else None),
                 price=p.get("marketPrice"), price_text=" ".join(x for x in [p.get("priceQualifier"), f'£{p.get("marketPrice", 0):,}'] if x),
                 bedrooms=f.get("bedrooms"), bathrooms=f.get("bathrooms"),
                 property_type=self.STYLES.get(p.get("style"), ""), images=[(p.get("image") or {}).get("mediumImage")] if (p.get("image") or {}).get("mediumImage") else [],
                 agent="Purplebricks", status_text="Under offer" if p.get("underOffer") else ("Sold STC" if p.get("sold") else ""))
        return l

    def fetch_detail(self, l):
        r = http.get(l["url"])
        t = flight(r.text)
        if '"property":{"id":' not in t: raise RuntimeError("Purplebricks detail: property object not found")
        i = t.index('"property":{"id":') + len('"property":')
        p, _ = json.JSONDecoder().raw_decode(t[i:])
        l["description"] = strip_html(resolve(t, p.get("description"))) or l["description"]
        l["features"] = p.get("starPoints") or []
        l["images"] = [x["url"] for x in p.get("images", []) if x.get("url")] or l["images"]
        fp = p.get("floorPlan") or {}
        l["floorplans"] = [fp["url"]] if fp.get("url") else []
        loc = p.get("location") or {}
        if loc.get("latitude"): l["lat"], l["lon"] = loc["latitude"], loc["longitude"]
        if loc.get("postcode"): l["postcode"] = loc["postcode"]
        ft = p.get("features") or {}
        l["bedrooms"] = ft.get("bedrooms") or l["bedrooms"]; l["bathrooms"] = ft.get("bathrooms") or l["bathrooms"]
        l["extra"].update(parking=ft.get("parking"), garage=ft.get("garage"), garden=ft.get("garden"), receptions=ft.get("receptions"),
                          ensuites=ft.get("ensuites"), toilets=ft.get("toilets"), pb_features=ft,
                          brochure=(p.get("links") or {}).get("brochureUrl"))
        ten = p.get("tenure") or {}
        l["tenure"] = (ten.get("summary") or "").lower()
        l["extra"]["council_tax"] = ((ten.get("councilTax") or {}).get("taxBand"))
        l["listed_at"] = p.get("publishDate") or l["listed_at"]
        l["detail_done"] = True
        return l
