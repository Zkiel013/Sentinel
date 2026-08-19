"""Sentinel: real-time crypto setup detection + alert server.

    python -m uvicorn sentinel.server:app --port 8777

Run from the project root (needs indicators.py on the path).
Detects setup occurrences and market conditions; it does not predict
profitability and is not financial advice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sentinel import advice, confluence, descriptions, detectors, notify, store
from sentinel import tradability, zones as zmod
from sentinel.market_data import MarketData
from sentinel.rules import DEFAULT_RULES, RuleEngine

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("sentinel")

DEFAULT_PREFS = {
    "symbols": ["BTCUSDT", "ETHUSDT"],
    "timeframes": ["1m", "5m", "15m", "1h", "4h", "1d"],
    "enabled_setups": sorted(detectors.DETECTORS),
    "channels": {"telegram": {}, "discord": {}, "email": {}},
    "min_feed_score": 0,
    "theme": "dark",
    "position_qty": 0.01,
    "leverages": [100, 200, 300, 400],
    "known_setups": [],
    "gate": dict(advice.DEFAULT_GATE),
    # "spot" or "futures". Futures pricing matches the funding/liquidation feeds
    # and what leveraged CFDs track, but its websocket is blocked in some regions
    # — including India — so spot is the safe default. Falls back automatically
    # if the chosen venue's stream never delivers.
    "market": "spot",
}

md: MarketData | None = None
engine = RuleEngine()
ui_clients: set[WebSocket] = set()
signal_feed: list[dict] = []          # rolling in-memory feed for the UI

# Zone building walks every timeframe's pivots, so it is the expensive part of
# a bar close. Cache per (symbol, tf) and rebuild only when that tf ticks over.
_analysis_cache: dict[tuple[str, str], dict] = {}


def prefs() -> dict:
    p = store.get_pref("prefs", None)
    if p is None:
        p = dict(DEFAULT_PREFS)
        p["known_setups"] = sorted(detectors.DETECTORS)
        store.set_pref("prefs", p)
        return p
    merged = {**DEFAULT_PREFS, **p}
    # Detectors added since prefs were last written would otherwise stay off
    # forever, because the saved enabled list is treated as authoritative.
    # `known_setups` records what the user has actually seen; anything newer
    # gets switched on once, and stays off after that only if switched off.
    known = set(merged.get("known_setups") or [])
    fresh = [s for s in detectors.DETECTORS if s not in known]
    if fresh:
        merged["enabled_setups"] = sorted(set(merged["enabled_setups"]) | set(fresh))
        merged["known_setups"] = sorted(known | set(detectors.DETECTORS))
        store.set_pref("prefs", merged)
        log.info("enabled %d new detector(s): %s", len(fresh), ", ".join(fresh))
    return merged


async def broadcast(payload: dict):
    dead = []
    for ws in ui_clients:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            dead.append(ws)
    for ws in dead:
        ui_clients.discard(ws)


async def on_tick(symbol: str, tf: str, bar: dict):
    if ui_clients:
        await broadcast({"kind": "tick", "symbol": symbol, "tf": tf, "bar": bar})


def build_analysis(symbol: str, tf: str, events: list[dict] | None = None) -> dict:
    """Score + zones + trade plan for one (symbol, timeframe).

    Synchronous and CPU-bound (pivot scans across every timeframe), so callers
    on the event loop should hand it to a thread.
    """
    p = prefs()
    frames = {t: md.df(symbol, t) for t in p["timeframes"]}
    frames = {t: d for t, d in frames.items() if d is not None and len(d) >= 60}
    if tf not in frames:
        return {}
    fr = md.funding_rate(symbol)
    snaps = {t: detectors.snapshot(d, fr) for t, d in frames.items()}
    ctx = {
        "tf": tf,
        "funding_history": md.funding_history(symbol),
        "liquidations": md.recent_liquidations(symbol, 60),
    }
    if events is None:
        events = detectors.run_detectors(frames[tf], snaps[tf], ctx,
                                         enabled=set(p["enabled_setups"]))
    zp = zmod.build(frames, tf)

    # session quality is computed first because it can itself be a gate
    timing = {"available": False, "reason": "timing unavailable"}
    try:
        # explicit None check: `a or b` on DataFrames raises "truth value is
        # ambiguous", which silently turned the whole timing block into a no-op
        pace_df = frames.get("5m")
        if pace_df is None:
            pace_df = frames.get(tf)
        t = tradability.score_now(symbol, pace_df)
        timing = {k: t.get(k) for k in
                  ("available", "score", "tier", "session", "now_ist",
                   "day_type", "next_prime", "reason")}
    except Exception:
        log.exception("timing score failed %s", symbol)

    out = advice.analyze(
        symbol, tf, frames, snaps, events, zp, ctx,
        qty=float(p.get("position_qty") or 0.01),
        leverages=tuple(p.get("leverages") or (100, 200, 300, 400)),
        gate=p.get("gate"),
        timing_score=timing.get("score") if timing.get("available") else None)
    out["timing"] = timing
    out["ts"] = time.time()
    out["bar_ts"] = frames[tf].index[-1].isoformat()
    _analysis_cache[(symbol, tf)] = out
    return out


async def on_bar_close(symbol: str, tf: str):
    p = prefs()
    df = md.df(symbol, tf)
    if len(df) < 50:
        return

    snap = detectors.snapshot(df, md.funding_rate(symbol))
    ctx = {
        "tf": tf,
        "funding_history": md.funding_history(symbol),
        "liquidations": md.recent_liquidations(symbol, 60),
    }
    events = detectors.run_detectors(df, snap, ctx,
                                     enabled=set(p["enabled_setups"]))
    conf = confluence.score(events, snap)

    try:
        analysis = await asyncio.to_thread(build_analysis, symbol, tf, events)
    except Exception:
        log.exception("analysis failed %s %s", symbol, tf)
        analysis = {}
    if analysis:
        await broadcast({"kind": "analysis", "symbol": symbol, "tf": tf,
                         "analysis": analysis})

    # always stream the bar + any raw signals to the dashboard
    msg = {
        "kind": "bar",
        "symbol": symbol, "tf": tf, "ts": df.index[-1].isoformat(),
        "close": snap["close"], "events": events,
        "confluence": conf, "snapshot": {k: v for k, v in snap.items()},
        # full OHLC so the client can append the closed bar instead of
        # refetching 400 candles on every close
        "bar": {"time": int(df.index[-1].timestamp()),
                "open": float(df["open"].iloc[-1]),
                "high": float(df["high"].iloc[-1]),
                "low": float(df["low"].iloc[-1]),
                "close": float(df["close"].iloc[-1]),
                "volume": float(df["volume"].iloc[-1])},
    }
    if events:
        entry = {
            "ts": time.time(), "symbol": symbol, "tf": tf,
            "events": events, "confluence": conf,
            "explanation": confluence.explain(symbol, tf, events, conf, snap),
        }
        signal_feed.insert(0, entry)
        del signal_feed[500:]
        msg["kind"] = "signals"
        msg["explanation"] = entry["explanation"]
    await broadcast(msg)

    # rule engine -> alerts
    rules = store.get_rules()
    for rule in engine.evaluate(rules, symbol, tf, events, snap, conf):
        alert = {
            "ts": time.time(), "symbol": symbol, "tf": tf,
            "rule_id": rule["id"], "rule_name": rule["name"],
            "score": conf["score"], "direction": conf["direction"],
            "priority": rule.get("priority", "normal"),
            "setups": [e["setup"] for e in events],
            "message": confluence.explain(symbol, tf, events, conf, snap),
        }
        store.save_alert(alert)
        await broadcast({"kind": "alert", "alert": alert,
                         "channels": rule.get("channels", [])})
        server_side = [c for c in rule.get("channels", [])
                       if c in ("telegram", "discord", "email")]
        if server_side:
            await asyncio.to_thread(
                notify.dispatch, server_side, p["channels"],
                f"[Sentinel] {rule['name']} — {symbol} {tf}",
                alert["message"])
        log.info("ALERT %s %s %s score=%d", rule["name"], symbol, tf,
                 conf["score"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    global md
    store.init()
    if not store.get_rules():
        for r in DEFAULT_RULES:
            store.put_rule(r)
    p = prefs()
    md = MarketData(p["symbols"], p["timeframes"],
                    market=p.get("market", "spot"))
    tradability.set_market(md.market)
    await asyncio.to_thread(md.warmup)
    md.on_bar_close = on_bar_close
    md.on_tick = on_tick
    task = asyncio.create_task(md.run())

    async def warm_timing():
        """Session profiles need ~90 days of hourly history per symbol, so they
        are built off the critical path — the UI shows them as pending meanwhile.

        Concurrently, not in sequence: built one after another, the second symbol
        stayed unavailable for the first half-minute and looked broken.
        """
        async def one(sym):
            try:
                await asyncio.to_thread(tradability.get_profile, sym)
                log.info("timing profile ready: %s", sym)
            except Exception:
                log.exception("timing profile failed: %s", sym)

        await asyncio.gather(*(one(s) for s in p["symbols"]))

    timing_task = asyncio.create_task(warm_timing())
    log.info("python %s, venv=%s (%s)", sys.version.split()[0],
             sys.prefix != sys.base_prefix, sys.prefix)
    log.info("sentinel running: %s x %s", p["symbols"], p["timeframes"])
    yield
    task.cancel()
    timing_task.cancel()


app = FastAPI(title="Sentinel", lifespan=lifespan)


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/strategies")
async def strategies():
    """Detector reference — every setup's exact long/short/neutral conditions."""
    return FileResponse(Path(__file__).parent / "static" / "strategies.html")


@app.get("/api/state")
async def state():
    p = prefs()
    return {
        "connected": md.connected if md else False,
        "symbols": p["symbols"], "timeframes": p["timeframes"],
        "enabled_setups": p["enabled_setups"],
        "all_setups": sorted(detectors.DETECTORS),
        "categories": detectors.SETUP_CATEGORIES,
        "min_feed_score": p.get("min_feed_score", 0),
        "funding": {s: md.funding_rate(s) for s in p["symbols"]} if md else {},
        "scalp_playbook": detectors.SCALP_PLAYBOOK,
        "theme": p.get("theme", "dark"),
        "position_qty": p.get("position_qty", 0.01),
        "leverages": p.get("leverages", [100, 200, 300, 400]),
    }


@app.get("/api/analysis")
async def analysis(symbol: str = "BTCUSDT", tf: str = "5m", fresh: bool = False):
    """Score, advice, zones and position math for one symbol+timeframe."""
    if md is None:
        return {}
    cached = _analysis_cache.get((symbol, tf))
    if cached and not fresh:
        return cached
    return await asyncio.to_thread(build_analysis, symbol, tf)


@app.get("/api/zones")
async def get_zones(symbol: str = "BTCUSDT", tf: str = "5m"):
    """Just the dynamic S/R bands — cheap enough to poll on timeframe switch."""
    if md is None:
        return {"zones": []}
    p = prefs()
    frames = {t: md.df(symbol, t) for t in p["timeframes"]}
    frames = {t: d for t, d in frames.items() if d is not None and len(d) >= 60}
    if tf not in frames:
        return {"zones": []}
    return await asyncio.to_thread(zmod.build, frames, tf)


@app.get("/api/timing")
async def timing(symbol: str = "BTCUSDT"):
    """Session-quality score plus the full 24-hour IST map for one symbol."""
    live = md.df(symbol, "5m") if md else None
    return await asyncio.to_thread(tradability.score_now, symbol, live)


@app.get("/api/docs")
async def docs():
    """Every description the UI renders — copy lives server-side, not in the HTML."""
    return {**descriptions.all_docs(), "weights": confluence.SETUP_WEIGHTS}


@app.get("/api/candles")
async def candles(symbol: str = "BTCUSDT", tf: str = "5m", limit: int = 300):
    df = md.df(symbol, tf)
    if df.empty:
        return []
    df = df.tail(limit)
    return [{"time": int(t.timestamp()), "open": r.open, "high": r.high,
             "low": r.low, "close": r.close, "volume": r.volume}
            for t, r in df.iterrows()]


@app.get("/api/feed")
async def feed():
    # min_feed_score existed in prefs and was reported by /api/state but was
    # never applied anywhere — a setting that silently did nothing. Honoured now.
    floor = int(prefs().get("min_feed_score") or 0)
    if floor <= 0:
        return signal_feed[:200]
    return [f for f in signal_feed
            if (f.get("confluence") or {}).get("score", 0) >= floor][:200]


@app.get("/api/diagnostics")
async def diagnostics():
    """Health surface: silent failures are the ones that matter."""
    p = prefs()
    return {
        # Windows venvs use a launcher shim, so the OS reports the *base*
        # interpreter as the executable path even when running inside the venv.
        # sys.prefix vs sys.base_prefix is the only reliable test, and it has to
        # be asked from inside the process.
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "prefix": sys.prefix,
            "in_venv": sys.prefix != sys.base_prefix,
        },
        "connected": md.connected if md else False,
        "market": md.market if md else None,
        "fell_back_to": md.fell_back_to if md else None,
        # the number that actually tells you the feed is alive
        "seconds_since_last_message": (
            round(time.time() - md.last_msg_at, 1)
            if md and md.last_msg_at else None),
        "symbols": p["symbols"],
        "bars": ({f"{s}/{t}": len(md.df(s, t)) for s in p["symbols"]
                  for t in p["timeframes"]} if md else {}),
        "detector_errors": detectors.detector_errors(),
        "timing_profiles": {s: bool(tradability._cache.get(s))
                            for s in p["symbols"]},
        "timing_building": [s for s in p["symbols"] if tradability.building(s)],
        "analysis_cached": [f"{k[0]}/{k[1]}" for k in _analysis_cache],
        "funding": {s: md.funding_rate(s) for s in p["symbols"]} if md else {},
        "gate": p.get("gate"),
    }


@app.get("/api/alerts")
async def alerts():
    return store.recent_alerts()


@app.get("/api/rules")
async def list_rules():
    return store.get_rules()


@app.post("/api/rules")
async def save_rule(rule: dict):
    if not rule.get("id"):
        rule["id"] = f"rule-{int(time.time() * 1000)}"
    store.put_rule(rule)
    return {"ok": True, "id": rule["id"]}


@app.delete("/api/rules/{rule_id}")
async def remove_rule(rule_id: str):
    store.delete_rule(rule_id)
    return {"ok": True}


@app.get("/api/prefs")
async def get_prefs():
    return prefs()


@app.post("/api/prefs")
async def set_prefs(body: dict):
    # capture the old values *before* writing: the previous version compared the
    # new prefs against a fresh read of the store it had just written, so
    # restart_needed was always False and adding a symbol never warned
    old = prefs()
    p = {**old, **body}
    store.set_pref("prefs", p)

    # Settings that feed into scoring (the gate, position size, leverages) must
    # invalidate the cached analyses, otherwise /api/analysis keeps serving a
    # verdict computed under the old settings until the next bar closes and the
    # change looks like it did nothing.
    if any(k in body for k in ("gate", "position_qty", "leverages",
                               "enabled_setups")):
        _analysis_cache.clear()

    return {"ok": True, "restart_needed": (
        set(p.get("symbols") or []) != set(old.get("symbols") or [])
        or set(p.get("timeframes") or []) != set(old.get("timeframes") or []))}


@app.post("/api/test-alert")
async def test_alert(body: dict):
    alert = {
        "ts": time.time(), "symbol": "TEST", "tf": "-",
        "rule_id": "test", "rule_name": "Test alert",
        "score": 78, "direction": "long",
        "priority": body.get("priority", "normal"),
        "setups": ["ema_pullback", "funding_extreme"],
        "message": "Test alert: this is what a triggered rule looks like. "
                   "Confluence score: 78/100. Setup occurrence only — not advice.",
    }
    await broadcast({"kind": "alert", "alert": alert,
                     "channels": body.get("channels", ["browser", "sound"])})
    p = prefs()
    server_side = [c for c in body.get("channels", [])
                   if c in ("telegram", "discord", "email")]
    if server_side:
        await asyncio.to_thread(notify.dispatch, server_side, p["channels"],
                                "[Sentinel] Test alert", alert["message"])
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    ui_clients.add(ws)
    try:
        while True:
            await ws.receive_text()   # keepalive pings from client
    except WebSocketDisconnect:
        ui_clients.discard(ws)


app.mount("/static", StaticFiles(
    directory=Path(__file__).parent / "static"), name="static")
