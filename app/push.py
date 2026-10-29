"""Web Push (VAPID).  Works in Chrome/Edge/Firefox/Android, and iOS/iPadOS 16.4+ ONLY when the app has been
added to the Home Screen.  Browsers require HTTPS (or localhost) for push - use Tailscale 'serve'/Cloudflare Tunnel."""
import json
from . import db

def vapid_keys():
    k = db.get_setting("vapid")
    if k: return k
    from py_vapid import Vapid
    from cryptography.hazmat.primitives import serialization
    import base64
    v = Vapid(); v.generate_keys()
    priv = v.private_pem().decode()
    pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    k = {"private_pem": priv, "public": base64.urlsafe_b64encode(pub).rstrip(b"=").decode()}
    db.set_setting("vapid", k)
    return k

def subscribe(sub):
    db.x("INSERT OR REPLACE INTO push_subs(endpoint,data,created) VALUES(?,?,strftime('%s','now'))", (sub["endpoint"], json.dumps(sub)))

def unsubscribe(endpoint):
    db.x("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))

def send_all(n):
    subs = db.q("SELECT * FROM push_subs")
    if not subs: return 0
    from pywebpush import webpush, WebPushException
    k = vapid_keys(); sent = 0
    payload = json.dumps({"title": n["title"], "body": n["body"], "url": n.get("url", "/"), "tag": f'l{n["listing_id"]}'})
    for s in subs:
        try:
            webpush(json.loads(s["data"]), payload, vapid_private_key=k["private_pem"] if False else _pem_file(k),
                    vapid_claims={"sub": "mailto:property-dashboard@localhost.invalid"}, timeout=15)
            sent += 1
        except WebPushException as e:
            if e.response is not None and e.response.status_code in (404, 410): unsubscribe(s["endpoint"])
        except Exception:
            pass
    return sent

def _pem_file(k):
    from .config import DATA
    p = DATA / "vapid_private.pem"
    if not p.exists(): p.write_text(k["private_pem"]); 
    try: p.chmod(0o600)
    except Exception: pass
    return str(p)
