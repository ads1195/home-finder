import json, re
from .. import db, http
from .base import Source, blank, strip_html, parse_price

BASE = "https://www.rightmove.co.uk"

def devalue(arr):
    """Decode SvelteKit/'devalue' flattened arrays used in Rightmove's __PAGE_MODEL."""
    cache = {}
    def get(i):
        if not isinstance(i, int) or i < 0: return None
        if i in cache: return cache[i]
        v = arr[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str) and v[0] in ("Date", "Set", "Map", "BigInt", "null", "Object", "Array", "RegExp"):
                r = v[1] if len(v) > 1 else None; cache[i] = r; return r
            r = []; cache[i] = r
            r.extend(get(x) if isinstance(x, int) else x for x in v); return r
        if isinstance(v, dict):
            r = {}; cache[i] = r
            for k, x in v.items(): r[k] = get(x) if isinstance(x, int) else x
            return r
        cache[i] = v; return v
    return get(0)

class Rightmove(Source):
    type = "rightmove"

    def _location_id(self, name):
        key = "rm_loc:" + name.lower()
        r = db.q1("SELECT value FROM geocache WHERE key=?", (key,))
        if r: return json.loads(r["value"])
        j = http.get("https://los.rightmove.co.uk/typeahead", params={"query": name, "limit": 6, "exclude": "STREET"}, headers={"Accept": "application/json"}).json()
        val = None
        for m in j.get("matches", []):
            if m["type"] in ("REGION", "OUTCODE", "POSTCODE") and "west yorkshire" in m["displayName"].lower():
                val = f'{m["type"]}^{m["id"]}'; break
        import time
        db.x("INSERT OR REPLACE INTO geocache(key,value,created) VALUES(?,?,?)", (key, json.dumps(val), time.time()))
        return val

    def search(self, criteria, poll):
        out, seen = [], set()
        for name in criteria["search_locations"]:
            try:
                loc = self._location_id(name)
            except http.Blocked:
                raise
            except Exception as e:
                self.warnings.append(f"location {name}: {e}")
                continue
            if not loc:
                self.warnings.append(f"location {name}: not found on Rightmove"); continue
            for page in range(poll["max_pages_per_search"]):
                params = {"locationIdentifier": loc, "radius": "3.0", "sortType": "6", "channel": "BUY",
                          "index": page * 24, "minBedrooms": criteria["bedrooms_min"],
                          "minPrice": int(criteria["price_min"] * 0.9), "maxPrice": int(criteria["price_stretch_max"]),
                          "dontShow": "retirement,sharedOwnership", "includeSSTC": "false"}
                r = http.get(f"{BASE}/property-for-sale/find.html", params=params)
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
                if not m: raise RuntimeError("Rightmove: __NEXT_DATA__ not found (page layout changed?)")
                sr = json.loads(m.group(1))["props"]["pageProps"]["searchResults"]
                props = sr.get("properties", [])
                for p in props:
                    if p["id"] in seen: continue
                    seen.add(p["id"])
                    out.append(self._summary(p))
                if len(props) < 24: break
        return out

    def _summary(self, p):
        l = blank("rightmove", p["id"], f"{BASE}/properties/{p['id']}")
        l.update(title=f'{p.get("bedrooms") or "?"} bed {p.get("propertySubType") or "property"}',
                 address=p.get("displayAddress", ""), lat=(p.get("location") or {}).get("latitude"),
                 lon=(p.get("location") or {}).get("longitude"), bedrooms=p.get("bedrooms"),
                 bathrooms=p.get("bathrooms"), property_type=p.get("propertySubType") or "",
                 tenure=((p.get("tenure") or {}).get("tenureType") or "").lower(),
                 description=strip_html(p.get("summary", "")),
                 images=[i["srcUrl"] for i in p.get("images", [])[:3]],
                 agent=(p.get("customer") or {}).get("branchDisplayName", ""),
                 listed_at=(p.get("firstVisibleDate") or (p.get("listingUpdate") or {}).get("listingUpdateDate")))
        pr = p.get("price") or {}
        dp = (pr.get("displayPrices") or [{}])[0]
        l["price"] = pr.get("amount"); l["price_text"] = " ".join(x for x in [dp.get("displayPriceQualifier"), dp.get("displayPrice")] if x)
        l["extra"] = {"has_floorplan": bool(p.get("numberOfFloorplans")), "photo_count": p.get("numberOfImages")}
        lu = p.get("listingUpdate") or {}
        if lu.get("listingUpdateDate"): l["extra"]["rm_update"] = {"reason": lu.get("listingUpdateReason"), "ts": lu.get("listingUpdateDate")}
        if p.get("addedOrReduced"): l["extra"]["rm_added"] = str(p["addedOrReduced"])[:60]
        ds = str(p.get("displayStatus") or "")
        if re.search(r"sold|stc|offer|reserved|agreed|withdrawn", ds, re.I): l["status_text"] = ds
        return l

    def fetch_detail(self, l):
        r = http.get(f"{BASE}/properties/{l['source_id']}")
        h = r.text
        i = h.find("window.__PAGE_MODEL = ")
        if i < 0: raise RuntimeError("Rightmove detail: __PAGE_MODEL not found")
        obj, _ = json.JSONDecoder().raw_decode(h[i + len("window.__PAGE_MODEL = "):])
        d = devalue(json.loads(obj["data"]))["propertyData"]
        l["description"] = strip_html((d.get("text") or {}).get("description", "")) or l["description"]
        l["features"] = d.get("keyFeatures") or []
        l["images"] = [x["url"] for x in d.get("images", [])] or l["images"]
        l["floorplans"] = [x["url"] for x in d.get("floorplans", []) if x.get("url")]
        loc = d.get("location") or {}
        if loc.get("latitude"): l["lat"], l["lon"] = loc["latitude"], loc["longitude"]
        a = d.get("address") or {}
        if a.get("outcode"): l["postcode"] = f'{a["outcode"]} {a.get("incode") or ""}'.strip()
        l["tenure"] = ((d.get("tenure") or {}).get("tenureType") or l["tenure"] or "").lower()
        l["bedrooms"] = d.get("bedrooms") or l["bedrooms"]; l["bathrooms"] = d.get("bathrooms") or l["bathrooms"]
        l["property_type"] = d.get("propertySubType") or l["property_type"]
        l["extra"].update(sizings=d.get("sizings"), brochures=[b.get("url") for b in (d.get("brochures") or [])],
                          rm_stations=d.get("nearestStations"), epc=[e.get("url") for e in (d.get("epcGraphs") or [])],
                          virtual_tours=[v.get("url") for v in (d.get("virtualTours") or [])])
        cu = d.get("customer") or {}
        l["agent"] = cu.get("branchDisplayName") or l["agent"]
        l["extra"]["agent_phone"] = (d.get("contactInfo") or {}).get("telephoneNumbers", {}).get("localNumber") if isinstance(d.get("contactInfo"), dict) else None
        st = d.get("status") or {}
        l["status_text"] = "" if st.get("published", True) else "unpublished"
        l["detail_done"] = True
        return l
