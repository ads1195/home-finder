"""Default configuration. Everything here is editable from the Settings page
(stored in SQLite, table `settings`). Items marked PLACEHOLDER are guesses."""
import copy, os, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = pathlib.Path(os.environ.get("PROPDASH_DATA", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "images").mkdir(exist_ok=True)
(DATA / "references").mkdir(exist_ok=True)
(DATA / "models").mkdir(exist_ok=True)
DB_PATH = DATA / "dashboard.sqlite"

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
NOMINATIM_UA = "PersonalPropertyDashboard/1.0 (single-user home tool)"

DEFAULT_CONFIG = {
    "poll": {
        "enabled": True,
        "interval_minutes": 45,          # 30-60 recommended
        "request_delay_s": 1.5,          # polite delay between requests to the same host
        "max_pages_per_search": 3,
        "max_detail_fetches_per_cycle": 80,
        "analyse_photos_for_candidates": True,
        "max_photos_per_listing": 12,
    },
    "criteria": {
        # --- budget (400-450k; fetch to a stretch ceiling) ---
        "price_min": 400000,
        "price_max": 450000,
        "price_stretch_max": 500000,     # 'Just over budget' ceiling (configurable)
        "bedrooms_min": 4,               # HARD (two rooms used as home offices)
        "bathrooms_min": 0,              # PLACEHOLDER (0 = off)
        "property_types": ["detached", "semi-detached", "terraced", "end of terrace",
                           "town house", "bungalow", "cottage", "house", "link-detached",
                           "mews", "farmhouse", "barn conversion", "other"],   # PLACEHOLDER
        "exclude_property_types": ["flat", "apartment", "maisonette", "park home",
                                   "land", "retirement", "studio"],            # PLACEHOLDER
        "tenure": "any",                 # PLACEHOLDER: any | freehold | leasehold
        "must_keywords": [],             # PLACEHOLDER (e.g. garden, driveway, garage)
        "exclude_keywords": ["shared ownership", "retirement", "over 55", "over 60",
                             "park home", "auction", "holiday let", "land only",
                             "cash buyers only", "non-standard construction"],  # PLACEHOLDER
        # --- tri-state checklist: mode = must | nice | off.  must => hard-exclude ONLY when
        #     explicitly contradicted; 'not mentioned' stays in (flagged) ---
        "checklist": {
            "kitchen_family": {"label": "Kitchen-diner + adjoining family/snug/play space",
                               "mode": "must", "weight": 10},
            "wc_or_ensuite": {"label": "Downstairs WC/cloakroom (or upstairs en-suite)",
                              "mode": "must", "weight": 6},
            "utility": {"label": "Utility room", "mode": "must", "weight": 6},
            "parking": {"label": "Off-street parking (driveway / garage + drive)",
                        "mode": "must", "weight": 8},
            "not_overlooked": {"label": "Not overlooked / private / backs onto open land",
                               "mode": "nice", "weight": 6},
            "garden_size": {"label": "Decent-sized garden / plot",
                            "mode": "nice", "weight": 6},
        },
        "floorplan_can_contradict": False,   # if True, floorplan-OCR 'likely absent' counts as contradicted
        # --- workspace & extras: score-only boosts (keyword regex -> points) ---
        "workspace_boosts": {
            "study": [r"\bstudy\b", 3], "home office": [r"home[- ]office|\boffice\b", 3],
            "garden office / room / outbuilding": [r"garden (office|room|studio)|outbuilding|summer ?house|log cabin|annex", 4],
            "separate reception rooms": [r"(two|2|three|3|multiple|separate) (reception|living|sitting)|separate (lounge|living|dining|sitting)|second reception", 3],
            "loft conversion": [r"loft (conversion|room)|converted (loft|attic)|attic room", 3],
            "extension / potential": [r"extension|extended|potential to (extend|convert)|scope to", 2],
            "family garden": [r"(family|enclosed|private|lawn(ed)?|rear) garden", 2],
        },
        "score_weights": {
            "drive": 30,          # max pts for drive times (anchors weighted via anchor weight)
            "checklist": 35,      # scaled from checklist weights
            "workspace": 10,
            "similarity": 15,     # similarity to reference homes (if any uploaded)
            "layout": 5,          # floorplan area / room-count signals
            "station": 2,         # LOW default weight: small bonus only
            "price": 3,           # small bonus for being inside the main budget band
            "size": 14,           # floor area vs the ideal home (~1,430 sq ft; mild penalty below ~1,250; bigger is never penalised); unknown = neutral
            "ideal": 35,          # feature fit vs the ideal home (open-plan kitchen/family, lounge, utility, WC, en-suite, parking, garden ...)
            "beyond": 3,          # small bonus for things that beat the ideal home
        },
        "size_target_sqft": 1430,   # (informational) the ideal home's floor area; scoring uses the ideal_home benchmark
        "min_score_to_notify": 0,
        # Curated "Top matches": only listings that pass ALL hard filters (no contradicted must-have)
        # AND score >= min_score are shown/notified; capped at max_count (best first).
        "top_matches": {"min_score": 65, "max_count": 10,
                        "just_over_min_score": 68, "just_over_max_count": 5},
        "notify_just_over_budget": True,
    },
    "sources": [
        {"id": "rightmove", "name": "Rightmove", "type": "rightmove", "enabled": True},
        {"id": "onthemarket", "name": "OnTheMarket", "type": "onthemarket", "enabled": True},
        {"id": "purplebricks", "name": "Purplebricks", "type": "purplebricks", "enabled": True},
        {"id": "zoopla", "name": "Zoopla", "type": "zoopla", "enabled": True},
    ],
    "ui": {"show_all_fetched": False},   # admin toggle: reveal the 'All fetched' and 'Needs review' tabs
    "notifiers": {
        "desktop": {"enabled": False},
        "webhook": {"enabled": False, "url": "", "format": "json"},   # json | slack | discord | ntfy
        "email": {"enabled": False, "smtp_host": "", "smtp_port": 587, "starttls": True,
                  "username": "", "password_env": "PROPDASH_SMTP_PASSWORD",
                  "from_addr": "", "to_addr": ""},
        "notify_baseline": False,   # don't push the very first batch externally
    },
    "vision": {
        "enabled": False,                 # auto-off without a key
        "provider": "openai",             # openai (any OpenAI-compatible) | anthropic
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "PROPDASH_VISION_API_KEY",
        "api_key": "",                    # optional; env var preferred
        "max_images": 6,
    },
    "placeholders": ["bathrooms_min", "property_types", "exclude_property_types", "tenure",
                     "must_keywords", "exclude_keywords", "search_area", "workspace_boosts",
                     "score_weights", "anchor coordinates (street midpoints)"],
}

def default_config():
    from . import private
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if os.environ.get("PROPDASH_LEAN") == "1":   # small free hosts: fewer photos / detail fetches per cycle
        cfg["poll"].update(max_photos_per_listing=4, max_detail_fetches_per_cycle=80)
    return private.finalize_config(cfg)
