"""Pluggable notifiers. All OFF by default (Settings -> Notifications). Each notifier gets a dict:
{title, body, url, kind, listing_id, price, address}.  Add your own by registering in NOTIFIERS."""
import json, os, platform, shutil, smtplib, subprocess
from email.message import EmailMessage
import httpx

def desktop(cfg, n):
    sysname = platform.system()
    t, b = n["title"], n["body"]
    if sysname == "Linux" and shutil.which("notify-send"):
        subprocess.run(["notify-send", t, b], timeout=10)
    elif sysname == "Darwin":
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(b)} with title {json.dumps(t)}'], timeout=10)
    elif sysname == "Windows":
        ps = ("[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime];"
              "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('ToastText02');"
              f"$t.GetElementsByTagName('text')[0].AppendChild($t.CreateTextNode({json.dumps(t)}))|Out-Null;"
              f"$t.GetElementsByTagName('text')[1].AppendChild($t.CreateTextNode({json.dumps(b)}))|Out-Null;"
              "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('PropertyDashboard').Show([Windows.UI.Notifications.ToastNotification]::new($t))")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=15)
    else:
        raise RuntimeError("no desktop notification method on this OS (install notify-send)")

def webhook(cfg, n):
    fmt, url = cfg.get("format", "json"), cfg.get("url")
    if not url: raise RuntimeError("webhook url not set")
    text = f'{n["title"]}\n{n["body"]}\n{n["url"]}'
    if fmt == "slack": r = httpx.post(url, json={"text": text}, timeout=15)
    elif fmt == "discord": r = httpx.post(url, json={"content": text}, timeout=15)
    elif fmt == "ntfy": r = httpx.post(url, content=n["body"].encode(), headers={"Title": n["title"], "Click": n["url"]}, timeout=15)
    else: r = httpx.post(url, json=n, timeout=15)
    r.raise_for_status()

def email(cfg, n):
    for k in ("smtp_host", "from_addr", "to_addr"):
        if not cfg.get(k): raise RuntimeError(f"email.{k} not set")
    m = EmailMessage(); m["Subject"] = n["title"]; m["From"] = cfg["from_addr"]; m["To"] = cfg["to_addr"]
    m.set_content(f'{n["body"]}\n\n{n["url"]}')
    with smtplib.SMTP(cfg["smtp_host"], int(cfg.get("smtp_port", 587)), timeout=20) as s:
        if cfg.get("starttls", True): s.starttls()
        pw = os.environ.get(cfg.get("password_env") or "") or cfg.get("password")
        if cfg.get("username") and pw: s.login(cfg["username"], pw)
        s.send_message(m)

NOTIFIERS = {"desktop": desktop, "webhook": webhook, "email": email}

def dispatch(cfg_notifiers, n, baseline=False):
    """returns list of (name, ok, msg)"""
    res = []
    if baseline and not cfg_notifiers.get("notify_baseline"):
        return [("all", False, "baseline batch: external notifiers skipped")]
    for name, fn in NOTIFIERS.items():
        c = cfg_notifiers.get(name) or {}
        if not c.get("enabled"): continue
        try: fn(c, n); res.append((name, True, "sent"))
        except Exception as e: res.append((name, False, f"{type(e).__name__}: {e}"))
    return res
