"""Polite shared HTTP client: browser UA, per-host rate limit, Cloudflare/captcha detection."""
import threading, time
from urllib.parse import urlparse
import httpx
from .config import USER_AGENT
from . import db

class Blocked(Exception):
    def __init__(self, reason, status=None):
        super().__init__(reason); self.reason = reason; self.status = status

HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-GB,en;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
_last = {}
_lock = threading.Lock()
client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=25)

def _delay():
    try:
        return float(db.config()["poll"].get("request_delay_s", 1.5))
    except Exception:
        return 1.5

def wait(host):
    with _lock:
        t = _last.get(host, 0) + _delay() - time.time()
        if t > 0:
            time.sleep(t)
        _last[host] = time.time()

def classify_block(r):
    body = r.text[:6000].lower() if "text" in r.headers.get("content-type", "text") else ""
    if r.status_code in (401, 403, 429, 503) or "just a moment" in body or "captcha" in body \
            or "attention required" in body or "access denied" in body:
        server = r.headers.get("server", "")
        why = "Cloudflare bot challenge ('Just a moment...')" if ("just a moment" in body or "cloudflare" in server.lower() and r.status_code == 403) \
            else "captcha/bot wall" if "captcha" in body else f"HTTP {r.status_code}"
        return why
    return None

def get(url, params=None, headers=None, check=True, **kw):
    host = urlparse(url).netloc
    wait(host)
    try:
        r = client.get(url, params=params, headers=headers, **kw)
    except httpx.HTTPError as e:
        raise RuntimeError(f"network error: {type(e).__name__}: {e}")
    if check:
        why = classify_block(r)
        if why:
            raise Blocked(f"{why} on {host}", r.status_code)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} for {url}")
    return r

def get_json(url, params=None, headers=None):
    return get(url, params=params, headers=headers).json()
