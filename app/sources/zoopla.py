from .. import http
from .base import Source

class Zoopla(Source):
    """Zoopla sits behind a Cloudflare managed challenge for non-browser clients.
    We make ONE plain polite request per cycle to learn whether that is still the case;
    we do not try to bypass the challenge.  If Zoopla ever serves plain HTML again the
    search can be implemented here (page embeds __NEXT_DATA__ with listings)."""
    type = "zoopla"
    def search(self, criteria, poll):
        r = http.get("https://www.zoopla.co.uk/for-sale/property/leeds/",
                     params={"q": "Leeds", "radius": "3", "beds_min": criteria["bedrooms_min"]})
        # not blocked -> parser not implemented for this layout
        raise RuntimeError("Zoopla reachable but no parser implemented for its current HTML")
