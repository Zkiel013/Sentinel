"""SQLite persistence: alert history, rules, user preferences."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "sentinel.db"

_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, symbol TEXT, tf TEXT, rule_id TEXT, rule_name TEXT,
            score INTEGER, direction TEXT, priority TEXT,
            setups TEXT, message TEXT
        );
        CREATE TABLE IF NOT EXISTS rules (
            id TEXT PRIMARY KEY, body TEXT
        );
        CREATE TABLE IF NOT EXISTS prefs (
            key TEXT PRIMARY KEY, body TEXT
        );
        """)


def save_alert(a: dict):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO alerts (ts,symbol,tf,rule_id,rule_name,score,direction,"
            "priority,setups,message) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (a["ts"], a["symbol"], a["tf"], a["rule_id"], a["rule_name"],
             a["score"], a["direction"], a["priority"],
             json.dumps(a["setups"]), a["message"]))


def recent_alerts(limit: int = 200) -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["setups"] = json.loads(d["setups"])
        out.append(d)
    return out


def get_rules() -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT body FROM rules").fetchall()
    return [json.loads(r["body"]) for r in rows]


def put_rule(rule: dict):
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO rules (id, body) VALUES (?, ?)",
                  (rule["id"], json.dumps(rule)))


def delete_rule(rule_id: str):
    with _lock, _conn() as c:
        c.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


def get_pref(key: str, default=None):
    with _lock, _conn() as c:
        row = c.execute("SELECT body FROM prefs WHERE key = ?", (key,)).fetchone()
    return json.loads(row["body"]) if row else default


def set_pref(key: str, value):
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO prefs (key, body) VALUES (?, ?)",
                  (key, json.dumps(value)))
