from .rightmove import Rightmove
from .onthemarket import OnTheMarket
from .purplebricks import Purplebricks
from .zoopla import Zoopla
from .agent import AgentSite

def make_source(cfg):
    t = cfg["type"]
    if t == "rightmove": return Rightmove(cfg)
    if t == "onthemarket": return OnTheMarket(cfg)
    if t == "purplebricks": return Purplebricks(cfg)
    if t == "zoopla": return Zoopla(cfg)
    if t in ("propertyfile", "wppf", "propertyhive", "generic"): return AgentSite(cfg)
    raise ValueError(f"unknown source type {t}")
