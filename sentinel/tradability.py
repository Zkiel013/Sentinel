"""When is it actually worth trading? A 0-100 session-quality score.

Built from the symbol's own 90-day hourly history rather than from generic
forex-session lore, because crypto's liquidity clock is its own thing. Every
threshold is a percentile inside that symbol's own distribution, so the score
self-scales across assets and regimes.

Four components, and the reason there are four rather than just volume:

  liquidity     trade *count*, not turnover. One whale print inflates volume
                without adding participants; trade count measures breadth,
                which is what actually tightens the spread.
  range         is the hour's typical range big enough to clear fees and
                spread — and not so big that a scalp stop is noise.
  efficiency    |close-open| / (high-low). High volume hours are often the
                *whippiest*: on BTC, 19:00-21:00 IST has triple the trade
                count of 02:00 IST but worse efficiency and more wick. Volume
                alone would score those hours as ideal; they are not.
  integrity     inverse of thin-and-wicky. A low trade count with a high wick
                share is the signature of stop-hunting in a shallow book,
                which is the condition the user asked to be warned about.

Separate weekday and weekend profiles, since weekend crypto is a materially
different market (roughly half the participants and half the range).

Times are reported in IST because that is where this is traded from.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

log = logging.getLogger("sentinel.timing")

# Follows the live candle venue, otherwise the session profile would describe one
# order book while the chart shows another. Set by the server at startup.
SPOT_KLINES = "https://api.binance.com/api/v3/klines"
FUT_KLINES = "https://fapi.binance.com/fapi/v1/klines"
KLINES_URL = SPOT_KLINES


def set_market(market: str):
    global KLINES_URL
    KLINES_URL = FUT_KLINES if market == "futures" else SPOT_KLINES
    _cache.clear()          # profiles from the other venue no longer apply
IST = timezone(timedelta(hours=5, minutes=30))

PROFILE_DAYS = 90
PROFILE_TTL = 6 * 3600          # rebuild twice a day; the clock drifts slowly

WEIGHTS = {"liquidity": 0.32, "range": 0.24, "efficiency": 0.22, "integrity": 0.22}

# Round-trip cost of one scalp as a fraction of price (spread + commission).
# The range component is scored against this, not against an absolute range.
ROUND_TRIP_COST = 0.0008

# Binance perpetual funding settles at these UTC hours; price routinely jerks
# around them, which is volatility without information for a scalper.
FUNDING_UTC_HOURS = (0, 8, 16)
EVENT_WINDOW_MIN = 15

_cache: dict[str, dict] = {}
_building: set[str] = set()
_building_lock = threading.Lock()


# ---------------------------------------------------------------- sessions

SESSIONS = (("Asia (Tokyo)", 0, 9), ("London", 8, 17), ("New York", 13, 22))


def ist_hour_to_utc(ist_hour: int) -> tuple[int, int]:
    """UTC start minute-of-day and start hour for an IST clock hour.

    IST is UTC+5:30, so every IST hour straddles two UTC hours — 03:00 IST is
    21:30 UTC. Taking the modal UTC hour of the bars in that bucket (the first
    attempt) flipped labels arbitrarily at session boundaries, so the start of
    the hour is used instead.
    """
    start_min = (ist_hour * 60 - 330) % 1440
    return start_min, start_min // 60


def session_label(ist_hour: int) -> str:
    """Which TradFi desks are live across this IST hour.

    Crypto trades 24/7 but its liquidity follows these desks, which is why the
    measured volume profile has the shape it does. Both UTC hours the IST hour
    covers are considered, so a straddling hour reports every session it touches.
    """
    start_min, _ = ist_hour_to_utc(ist_hour)
    covered = {(start_min // 60) % 24, ((start_min + 59) // 60) % 24}
    live = {name for name, a, b in SESSIONS if any(a <= h < b for h in covered)}
    if {"London", "New York"} <= live:
        return "London + New York overlap"
    for name in ("New York", "London", "Asia (Tokyo)"):
        if name in live:
            return name
    return "off-hours (post-NY, pre-Asia)"


def utc_span(ist_hour: int) -> str:
    start_min, _ = ist_hour_to_utc(ist_hour)
    end_min = (start_min + 60) % 1440
    return (f"{start_min // 60:02d}:{start_min % 60:02d}–"
            f"{end_min // 60:02d}:{end_min % 60:02d} UTC")


# ---------------------------------------------------------------- history

def _fetch_hourly(symbol: str, days: int = PROFILE_DAYS) -> pd.DataFrame:
    end = int(time.time() * 1000)
    start = end - days * 86_400_000
    step = 3_600_000
    rows: list = []
    while start < end:
        r = requests.get(KLINES_URL, params={
            "symbol": symbol, "interval": "1h",
            "startTime": start, "limit": 1000}, timeout=30).json()
        if not isinstance(r, list) or not r:
            break
        rows += r
        start = r[-1][0] + step
        if len(r) < 1000:
            break
        time.sleep(0.15)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "t", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_base", "taker_quote", "ignore"])
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[c] = df[c].astype(float)
    df["trades"] = df["trades"].astype(int)
    df = df.drop_duplicates("t")
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df


def _bar_metrics(df: pd.DataFrame) -> pd.DataFrame:
    span = (df["high"] - df["low"]).replace(0, np.nan)
    body = (df["close"] - df["open"]).abs()
    ist = df.index.tz_convert(IST)
    return pd.DataFrame({
        "ist_hour": ist.hour,
        "utc_hour": df.index.hour,
        "weekend": ist.dayofweek >= 5,
        "volume": df["volume"].values,
        "quote_volume": df["quote_volume"].values,
        "trades": df["trades"].values,
        "range_pct": (span / df["close"]).values,
        "efficiency": (body / span).values,
        "wick_share": ((span - body) / span).values,
    }, index=df.index)


def _pctile_fn(series: pd.Series):
    """Absolute percentile inside the symbol's own distribution of all bars.

    Ranking hour medians only against each other would force a spread from 0 to
    100 even if every hour were fine. Ranking against every bar keeps the score
    meaningful in absolute terms.
    """
    clean = series.dropna().to_numpy()
    if not len(clean):
        return lambda v: 0.5
    ordered = np.sort(clean)
    return lambda v: (float(np.searchsorted(ordered, v) / len(ordered))
                      if v is not None and np.isfinite(v) else 0.5)


def _score_hour(row: dict, pct: dict) -> dict:
    liquidity = pct["trades"](row["trades"])

    # Range is not "more is better": it has to clear costs, then it stops
    # helping and starts making stops unusable.
    cost_mult = (row["range_pct"] / ROUND_TRIP_COST) if row["range_pct"] else 0
    if cost_mult <= 1.5:
        rng = 0.05                      # cannot pay for itself
    elif cost_mult >= 14:
        rng = 0.62                      # plenty of movement, but stops get wide
    else:
        rng = min(1.0, 0.10 + 0.90 * (cost_mult - 1.5) / 7.0)

    efficiency = pct["efficiency"](row["efficiency"])

    # thin + wicky = hunting conditions. Both have to be bad to score badly.
    thin = 1.0 - liquidity
    wicky = pct["wick_share"](row["wick_share"])
    integrity = 1.0 - min(1.0, thin * 0.55 + wicky * 0.45)

    parts = {"liquidity": liquidity, "range": rng,
             "efficiency": efficiency, "integrity": integrity}
    score = sum(WEIGHTS[k] * v for k, v in parts.items())
    return {"score": int(round(max(1, min(100, score * 100)))),
            "parts": {k: round(v, 3) for k, v in parts.items()},
            "cost_multiple": round(cost_mult, 1)}


def tier(score: int) -> str:
    return ("prime" if score >= 72 else "good" if score >= 58
            else "fair" if score >= 44 else "poor" if score >= 30 else "avoid")


def build_profile(symbol: str) -> dict:
    """Per-IST-hour session quality for one symbol, weekday and weekend."""
    df = _fetch_hourly(symbol)
    if df.empty or len(df) < 24 * 14:
        log.warning("timing profile for %s: not enough history (%d bars)",
                    symbol, len(df))
        return {}
    m = _bar_metrics(df)
    pct = {k: _pctile_fn(m[k]) for k in
           ("trades", "quote_volume", "efficiency", "wick_share", "range_pct")}

    out: dict = {"symbol": symbol, "built_at": time.time(),
                 "bars": int(len(m)),
                 "from": m.index[0].isoformat(), "to": m.index[-1].isoformat(),
                 "hours": {}}
    for weekend in (False, True):
        sub = m[m["weekend"] == weekend]
        key = "weekend" if weekend else "weekday"
        hours = []
        for h in range(24):
            hb = sub[sub["ist_hour"] == h]
            if hb.empty:
                hours.append(None)
                continue
            row = {c: float(hb[c].median()) for c in
                   ("trades", "quote_volume", "range_pct", "efficiency",
                    "wick_share", "volume")}
            sc = _score_hour(row, pct)
            hours.append({
                "ist_hour": h,
                "utc_span": utc_span(h),
                "session": session_label(h),
                "score": sc["score"],
                "tier": tier(sc["score"]),
                "parts": sc["parts"],
                "cost_multiple": sc["cost_multiple"],
                "median_trades": int(row["trades"]),
                "median_usd_volume": round(row["quote_volume"]),
                "median_volume": row["volume"],
                "median_range_pct": round(row["range_pct"] * 100, 3),
                "median_efficiency": round(row["efficiency"], 3),
                "median_wick_share": round(row["wick_share"], 3),
                "samples": int(len(hb)),
            })
        out["hours"][key] = hours

    wk = [h for h in out["hours"]["weekday"] if h]
    we = [h for h in out["hours"]["weekend"] if h]
    out["weekend_penalty"] = (
        round(np.mean([h["score"] for h in we]) - np.mean([h["score"] for h in wk]), 1)
        if wk and we else 0.0)
    out["best_windows"] = _windows(out["hours"]["weekday"], above=58)
    # threshold sits above the weekday minimum on purpose: a strict "< worst
    # score" test returns nothing, because some hour always is the minimum.
    # min_hours=1 here — an isolated bad hour is still worth being told about,
    # unlike an isolated good one.
    out["worst_windows"] = _windows(out["hours"]["weekday"], below=50, min_hours=1)
    return out


def _windows(hours: list, above: int | None = None, below: int | None = None,
             min_hours: int = 2) -> list[dict]:
    """Contiguous runs of hours passing a threshold, wrapping past midnight.

    Single-hour runs are dropped — one hour poking over the line is sampling
    noise, not a window someone can plan a session around.
    """
    ok = [bool(h) and ((above is not None and h["score"] >= above)
                       or (below is not None and h["score"] < below))
          for h in hours]
    if not any(ok):
        return []
    runs, start = [], None
    for i in range(24):
        if ok[i] and start is None:
            start = i
        elif not ok[i] and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, 23))
    # stitch a run that wraps midnight
    if len(runs) > 1 and runs[0][0] == 0 and runs[-1][1] == 23:
        runs[0] = (runs[-1][0], runs[0][1])
        runs.pop()
    out = []
    for a, b in runs:
        idx = [*range(a, 24), *range(0, b + 1)] if a > b else list(range(a, b + 1))
        if len(idx) < min_hours:
            continue
        out.append({
            "from_ist": f"{a:02d}:00",
            "to_ist": f"{(b + 1) % 24:02d}:00",
            "hours": len(idx),
            "avg_score": int(round(np.mean(
                [hours[i]["score"] for i in idx if hours[i]]))),
            "sessions": sorted({session_label(i) for i in idx}),
        })
    return out


def get_profile(symbol: str, force: bool = False, block: bool = True) -> dict:
    """Cached session profile.

    With block=False a cache miss starts the build in the background and returns
    empty immediately. Building costs three paginated requests for 90 days of
    hourly history, so a symbol asked for before its warmup finished would
    otherwise make the caller wait seconds — which looks like the symbol being
    broken rather than pending.
    """
    p = _cache.get(symbol)
    fresh = p and time.time() - p.get("built_at", 0) < PROFILE_TTL
    if fresh and not force:
        return p
    if not block:
        _start_build(symbol)
        return p or {}
    built = build_profile(symbol)
    if built:
        _cache[symbol] = built
        return built
    return p or {}


def _start_build(symbol: str):
    """Kick off one background build per symbol, never two at once."""
    with _building_lock:
        if symbol in _building:
            return
        _building.add(symbol)

    def run():
        try:
            built = build_profile(symbol)
            if built:
                _cache[symbol] = built
                log.info("timing profile built in background: %s", symbol)
        except Exception:
            log.exception("background timing profile failed: %s", symbol)
        finally:
            with _building_lock:
                _building.discard(symbol)

    threading.Thread(target=run, daemon=True,
                     name=f"timing-profile-{symbol}").start()


def building(symbol: str) -> bool:
    with _building_lock:
        return symbol in _building


# ---------------------------------------------------------------- live score

def _pace(live_df: pd.DataFrame | None, now_ist: datetime,
          expected_hour_volume: float) -> dict | None:
    """Is this specific hour running thinner than that hour normally does?

    A profile says what 19:00 IST is usually like. This says whether *today's*
    19:00 is showing up — a holiday or a dead tape can leave a normally prime
    hour empty, and that is exactly when a thin book gets pushed around.
    """
    if live_df is None or live_df.empty or not expected_hour_volume:
        return None
    hour_start = now_ist.replace(minute=0, second=0, microsecond=0)
    bars = live_df[live_df.index >= hour_start.astimezone(timezone.utc)]
    if bars.empty or len(live_df) < 2:
        return None
    elapsed_min = max((now_ist - hour_start).total_seconds() / 60, 1.0)

    # Only *closed* bars are in the frame, so the volume covers fewer minutes
    # than the wall clock. Dividing by wall-clock elapsed understated the pace
    # by up to a full bar right after an hour boundary — enough to report a
    # normal hour as thin. Project over the span the bars actually cover.
    bar_min = max(round((live_df.index[-1] - live_df.index[-2]).total_seconds() / 60), 1)
    covered_min = len(bars) * bar_min
    if covered_min < 10:
        return None                     # too little of the hour to extrapolate

    so_far = float(bars["volume"].sum())
    projected = so_far / (covered_min / 60.0)
    ratio = projected / expected_hour_volume
    # Volume arrives in bursts, so a projection from the first few minutes is
    # mostly noise. Confidence ramps with elapsed time and scales the penalty,
    # rather than letting one quiet opening print swing the score by 14 points.
    confidence = min(1.0, covered_min / 30.0)
    return {"ratio": round(ratio, 2),
            "elapsed_min": int(elapsed_min),
            "covered_min": int(covered_min),
            "confidence": round(confidence, 2),
            "so_far": round(so_far, 3),
            "projected": round(projected, 3),
            "expected": round(expected_hour_volume, 3)}


def score_now(symbol: str, live_df: pd.DataFrame | None = None,
              now: datetime | None = None, block: bool = False) -> dict:
    """Live 0-100 'is now a good time to trade' reading, plus the full IST map.

    Non-blocking by default: a symbol whose profile is still warming reports
    pending rather than holding the request open for the history download.
    """
    prof = get_profile(symbol, block=block)
    if not prof:
        return {"available": False, "pending": building(symbol),
                "reason": ("session profile is still building for this symbol — "
                           "it needs 90 days of hourly history, and refreshes in "
                           "a few seconds"
                           if building(symbol)
                           else "not enough history to build a session profile")}

    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_ist = now_utc.astimezone(IST)
    weekend = now_ist.weekday() >= 5
    key = "weekend" if weekend else "weekday"
    hours = prof["hours"][key] or prof["hours"]["weekday"]
    cur = hours[now_ist.hour] or prof["hours"]["weekday"][now_ist.hour]
    if not cur:
        return {"available": False, "reason": "no profile for this hour"}

    score = float(cur["score"])
    notes: list[str] = []
    adjustments: list[dict] = []

    pace = _pace(live_df, now_ist, cur["median_volume"])
    if pace:
        # an hour running at half its usual volume is not the hour the profile
        # describes, so the profile score has to be pulled toward reality
        if pace["ratio"] < 0.55:
            raw, note = -14, (f"this hour is running at {pace['ratio']:.0%} of its "
                              f"normal volume — unusually thin tape, wider spread "
                              f"and easier to push around")
        elif pace["ratio"] < 0.8:
            raw, note = -6, f"volume is {pace['ratio']:.0%} of normal for this hour"
        elif pace["ratio"] > 1.6:
            raw, note = +6, (f"volume is {pace['ratio']:.0%} of normal — the tape is "
                             f"unusually active")
        else:
            raw, note = 0, None
        delta = int(round(raw * pace["confidence"]))
        if delta:
            adjustments.append({
                "reason": f"live volume pace ({pace['covered_min']} min of closed bars,"
                          f" {pace['confidence']:.0%} confidence)",
                "delta": delta})
            score += delta
            if note:
                notes.append(note)

    if weekend:
        notes.append("weekend session: roughly half the weekday participation "
                     "and range, and the profile below reflects that")

    mins_to_funding = min(
        ((h - now_utc.hour) % 24) * 60 - now_utc.minute for h in FUNDING_UTC_HOURS)
    if abs(mins_to_funding) <= EVENT_WINDOW_MIN or mins_to_funding >= 24 * 60 - EVENT_WINDOW_MIN:
        adjustments.append({"reason": "funding settlement window", "delta": -8})
        score -= 8
        notes.append("inside the funding settlement window — price often jerks "
                     "here without it meaning anything")

    if now_utc.hour == 0 and now_utc.minute < EVENT_WINDOW_MIN:
        adjustments.append({"reason": "daily candle close", "delta": -5})
        score -= 5
        notes.append("daily candle close (05:30 IST) — expect a positioning wick")

    final = int(round(max(1, min(100, score))))

    # rank every hour so "when instead?" has an answer
    ranked = sorted([h for h in hours if h], key=lambda h: -h["score"])
    nxt = _next_window(hours, now_ist)

    return {
        "available": True,
        "symbol": symbol,
        "score": final,
        "tier": tier(final),
        "base_score": cur["score"],
        "adjustments": adjustments,
        "notes": notes,
        "now_ist": now_ist.strftime("%H:%M"),
        "now_utc": now_utc.strftime("%H:%M"),
        "day_type": key,
        "session": cur["session"],
        "current_hour": cur,
        "pace": pace,
        "hours": hours,
        "weekday_hours": prof["hours"]["weekday"],
        "weekend_hours": prof["hours"]["weekend"],
        "best_windows": prof["best_windows"],
        "worst_windows": prof["worst_windows"],
        "weekend_penalty": prof["weekend_penalty"],
        "best_hours": [h["ist_hour"] for h in ranked[:4]],
        "worst_hours": [h["ist_hour"] for h in ranked[-4:]],
        "next_prime": nxt,
        "profile_meta": {"bars": prof["bars"], "from": prof["from"],
                         "to": prof["to"], "days": PROFILE_DAYS},
        "advice": _advice(final, cur, nxt, notes, weekend),
        "downgraded": final < cur["score"] - 5,
    }


def _next_window(hours: list, now_ist: datetime) -> dict | None:
    """Next hour scoring 'good' or better, and how far away it is."""
    for ahead in range(1, 25):
        h = (now_ist.hour + ahead) % 24
        entry = hours[h]
        if entry and entry["score"] >= 58:
            starts = (now_ist.replace(minute=0, second=0, microsecond=0)
                      + timedelta(hours=ahead))
            return {"ist_hour": h, "in_minutes": int((starts - now_ist).total_seconds() // 60),
                    "at_ist": f"{h:02d}:00", "score": entry["score"],
                    "session": entry["session"]}
    return None


def _advice(score: int, cur: dict, nxt: dict | None,
            notes: list[str], weekend: bool) -> dict:
    t = tier(score)
    if t == "prime":
        head = (f"Prime window. {cur['session']} — {cur['median_trades']:,} trades "
                f"and {cur['median_range_pct']:.2f}% range in a typical "
                f"{cur['ist_hour']:02d}:00 IST hour.")
        verdict = "Best conditions of the day. Size and spread work in your favour here."
    elif t == "good":
        head = f"Workable. {cur['session']}, {cur['median_trades']:,} typical trades."
        verdict = "Tradable, but not the deepest book of the day."
    elif t == "fair":
        head = f"Thin-ish. {cur['session']}, {cur['median_trades']:,} typical trades."
        verdict = ("Marginal. Expect wider spreads and more wick than the numbers "
                   "on the Analysis tab assume.")
    else:
        head = (f"Poor window. {cur['session']}, only {cur['median_trades']:,} trades "
                f"and {cur['median_range_pct']:.2f}% range in a typical hour.")
        verdict = ("This is when a shallow book gets pushed through obvious levels. "
                   "At 100-400x that is the expensive kind of noise.")
    bits = [verdict]
    if score < cur["score"] - 5:
        bits.append(f"Note the gap: {cur['ist_hour']:02d}:00 IST normally scores "
                    f"{cur['score']} ({tier(cur['score'])}). Today it is worse than "
                    f"its own reputation, and the reasons are listed below.")
    if cur["cost_multiple"] < 4:
        bits.append(f"A typical hour's range is only {cur['cost_multiple']}x the "
                    f"round-trip cost, so the edge has to be large to survive fees.")
    if cur["parts"]["efficiency"] < 0.4:
        bits.append("This hour is historically choppy rather than directional — "
                    "high volume does not mean clean trends.")
    if nxt and t in ("fair", "poor", "avoid"):
        bits.append(f"Next decent window starts {nxt['at_ist']} IST "
                    f"({nxt['in_minutes']} min away, {nxt['session']}, scores "
                    f"{nxt['score']}).")
    return {"headline": head, "points": bits + notes}
