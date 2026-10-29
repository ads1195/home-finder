import time
from pathlib import Path
import httpx
from .config import USER_AGENT
_c = httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}, follow_redirects=True, timeout=30)
_last = [0.0]

def fetch_file(url, dest, delay=0.25):
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 500: return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    w = _last[0] + delay - time.time()
    if w > 0: time.sleep(w)
    _last[0] = time.time()
    try:
        r = _c.get(url)
        if r.status_code != 200 or len(r.content) < 500: return None
        ct = r.headers.get("content-type", "")
        if "pdf" in ct or "html" in ct: return None
        dest.write_bytes(r.content)
        return dest
    except Exception:
        return None
