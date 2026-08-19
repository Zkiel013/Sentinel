"""No-code rule engine: nested AND/OR condition trees stored as JSON.

Rule shape:
{
  "id": "...", "name": "...", "enabled": true,
  "symbols": ["BTCUSDT"], "timeframes": ["5m", "15m"],
  "min_confluence": 0, "min_confirmations": 1,
  "cooldown_sec": 900,
  "channels": ["browser", "sound", "telegram"],
  "priority": "normal" | "high",
  "logic": {"op": "AND", "items": [
      {"type": "setup", "name": "ema_pullback"},
      {"op": "OR", "items": [
          {"type": "setup", "name": "funding_extreme"},
          {"type": "indicator", "field": "close", "cmp": ">", "target": "ema200"}
      ]}
  ]}
}

Indicator conditions compare a snapshot field against a number ("value") or
another field ("target") — this is how users define custom setups without code.
"""

from __future__ import annotations

import time

CMP = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


def eval_node(node: dict, setups: set[str], snap: dict,
              conf: dict) -> bool:
    if "op" in node:
        results = [eval_node(x, setups, snap, conf) for x in node.get("items", [])]
        if not results:
            return False
        return all(results) if node["op"].upper() == "AND" else any(results)
    t = node.get("type")
    if t == "setup":
        return node.get("name") in setups
    if t == "indicator":
        a = snap.get(node.get("field"))
        if a is None:
            return False
        if "target" in node:
            b = snap.get(node["target"])
        else:
            b = node.get("value")
        if b is None:
            return False
        return CMP.get(node.get("cmp", ">"), CMP[">"])(a, b)
    if t == "confluence":
        return conf.get("score", 0) >= node.get("min", 0)
    return False


class RuleEngine:
    def __init__(self):
        self._last_fired: dict[tuple, float] = {}

    def evaluate(self, rules: list[dict], symbol: str, tf: str,
                 events: list[dict], snap: dict, conf: dict) -> list[dict]:
        setups = {e["setup"] for e in events}
        fired = []
        now = time.time()
        for rule in rules:
            if not rule.get("enabled", True):
                continue
            if rule.get("symbols") and symbol not in rule["symbols"]:
                continue
            if rule.get("timeframes") and tf not in rule["timeframes"]:
                continue
            if conf["score"] < rule.get("min_confluence", 0):
                continue
            if len(setups) < rule.get("min_confirmations", 1):
                continue
            key = (rule.get("id"), symbol, tf)
            if now - self._last_fired.get(key, 0) < rule.get("cooldown_sec", 300):
                continue
            if not eval_node(rule.get("logic", {}), setups, snap, conf):
                continue
            self._last_fired[key] = now
            fired.append(rule)
        return fired


DEFAULT_RULES = [
    {
        "id": "trend-pullback-funding",
        "name": "Trend pullback + funding tailwind",
        "enabled": True,
        "symbols": [], "timeframes": ["5m", "15m", "1h"],
        "min_confluence": 35, "min_confirmations": 1, "cooldown_sec": 900,
        "channels": ["browser", "sound"], "priority": "normal",
        "logic": {"op": "AND", "items": [
            {"type": "setup", "name": "ema_pullback"},
            {"op": "OR", "items": [
                {"type": "setup", "name": "funding_extreme"},
                {"type": "indicator", "field": "close", "cmp": ">",
                 "target": "ema200"},
            ]},
        ]},
    },
    {
        "id": "breakout-confluence",
        "name": "Breakout with volume + structure",
        "enabled": True,
        "symbols": [], "timeframes": ["5m", "15m"],
        "min_confluence": 40, "min_confirmations": 2, "cooldown_sec": 900,
        "channels": ["browser", "sound"], "priority": "high",
        "logic": {"op": "AND", "items": [
            {"op": "OR", "items": [
                {"type": "setup", "name": "orb"},
                {"type": "setup", "name": "structure_break"},
            ]},
            {"type": "setup", "name": "volume_spike"},
        ]},
    },
    {
        "id": "flow-events",
        "name": "Liquidation cascade or funding extreme",
        "enabled": True,
        "symbols": [], "timeframes": ["1m", "5m"],
        "min_confluence": 0, "min_confirmations": 1, "cooldown_sec": 600,
        "channels": ["browser", "sound"], "priority": "high",
        "logic": {"op": "OR", "items": [
            {"type": "setup", "name": "liquidation_cascade"},
            {"type": "setup", "name": "funding_extreme"},
        ]},
    },
]
