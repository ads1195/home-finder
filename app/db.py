import json, sqlite3, threading, time
from .config import DB_PATH, default_config

_local = threading.local()
_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS listings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT, address TEXT, postcode TEXT, lat REAL, lon REAL,
  price INTEGER, price_text TEXT, bedrooms INTEGER, bathrooms INTEGER,
  property_type TEXT, tenure TEXT, description TEXT, features TEXT, images TEXT, floorplans TEXT,
  agent TEXT, status_text TEXT, listed_at TEXT, extra TEXT,
  first_seen REAL, last_seen REAL, active INTEGER DEFAULT 1, detail_done INTEGER DEFAULT 0,
  street_key TEXT, hidden INTEGER DEFAULT 0, favourite INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_listings_pc ON listings(postcode);
CREATE TABLE IF NOT EXISTS sightings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER, source TEXT, source_id TEXT, url TEXT,
  price INTEGER, first_seen REAL, last_seen REAL, raw TEXT,
  UNIQUE(source, source_id)
);
CREATE TABLE IF NOT EXISTS routes(
  listing_id INTEGER, anchor TEXT, straight_km REAL, drive_min REAL, drive_km REAL, method TEXT,
  PRIMARY KEY(listing_id, anchor)
);
CREATE TABLE IF NOT EXISTS analysis(
  listing_id INTEGER PRIMARY KEY, data TEXT, updated REAL
);
CREATE TABLE IF NOT EXISTS evaluation(
  listing_id INTEGER PRIMARY KEY, data TEXT, score REAL, band TEXT, passes INTEGER, updated REAL
);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER, kind TEXT, message TEXT,
  created REAL, read INTEGER DEFAULT 0, baseline INTEGER DEFAULT 0, UNIQUE(listing_id, kind)
);
CREATE TABLE IF NOT EXISTS source_status(
  source TEXT PRIMARY KEY, data TEXT
);
CREATE TABLE IF NOT EXISTS geocache(key TEXT PRIMARY KEY, value TEXT, created REAL);
CREATE TABLE IF NOT EXISTS refs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, label TEXT, path TEXT, url TEXT, sha TEXT UNIQUE,
  features TEXT, embedding BLOB, created REAL, notes TEXT
);
CREATE TABLE IF NOT EXISTS push_subs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT UNIQUE, data TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, created REAL);
"""

def conn():
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        _local.c = c
    return c

def init():
    c = conn()
    c.executescript(SCHEMA)
    c.commit()
    if get_setting("config") is None:
        set_setting("config", default_config())
        set_setting("installed_at", time.time())

def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]

def q1(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None

def x(sql, args=()):
    with _write_lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur

def get_setting(key, default=None):
    r = conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default

def set_setting(key, value):
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(value)))

def config():
    cfg = get_setting("config") or default_config()
    # forward-compat: fill new default keys
    d = default_config()
    for k, v in d.items():
        cfg.setdefault(k, v)
    for k, v in d["criteria"].items():
        cfg["criteria"].setdefault(k, v)
    for k, v in d["poll"].items():
        cfg["poll"].setdefault(k, v)
    for k, v in d["criteria"]["score_weights"].items():
        cfg["criteria"]["score_weights"].setdefault(k, v)
    return cfg
