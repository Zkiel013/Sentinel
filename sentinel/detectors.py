"""Setup detectors.

Each detector inspects the latest CLOSED bar of a (symbol, timeframe) frame
and returns an event dict or None:

    {"setup": str, "direction": "long"|"short"|"neutral",
     "strength": 0..1, "detail": str}

Detectors flag that a condition occurred. They say nothing about whether
trading it is profitable — backtests of these same setups after costs say
mostly not.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

try:                       # normal case: imported as part of the package
    from . import indicators as ind
except ImportError:        # fallback: module run directly, not as a package
    import indicators as ind

log = logging.getLogger("sentinel.detectors")

INTRADAY_TFS = {"1m", "5m", "15m", "1h"}


def _last(series: pd.Series):
    v = series.iloc[-1]
    return float(v) if pd.notna(v) else None


def snapshot(df: pd.DataFrame, funding_rate: float | None) -> dict:
    """Indicator values at the latest closed bar; feeds rules + explanations."""
    c = df["close"]
    a = ind.atr(df, 14)
    vwap = ind.session_vwap(df)
    vol = df["volume"]
    v_mean = vol.rolling(48).mean()
    v_std = vol.rolling(48).std()
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    bb_width = (4 * std) / mid                      # (upper-lower)/mid at 2σ
    taker_buy = df["taker_buy_base"] if "taker_buy_base" in df else None
    snap = {
        "close": _last(c),
        "open": float(df["open"].iloc[-1]),
        "high": float(df["high"].iloc[-1]),
        "low": float(df["low"].iloc[-1]),
        "ema9": _last(ind.ema(c, 9)),
        "ema20": _last(ind.ema(c, 20)),
        "ema21": _last(ind.ema(c, 21)),
        "ema50": _last(ind.ema(c, 50)),
        "ema200": _last(ind.ema(c, 200)),
        "rsi_2": _last(ind.rsi(c, 2)),
        "rsi_14": _last(ind.rsi(c, 14)),
        "vwap": _last(vwap),
        "atr": _last(a),
        "atr_pct": _last(a / c),
        "vol_z": _last((vol - v_mean) / v_std),
        "vol_ratio": _last(vol / v_mean),
        "bb_z": _last((c - mid) / std),
        "bb_width": _last(bb_width),
        # where current band width sits in its own 120-bar history (0..1);
        # low = squeeze, high = expansion. Percentile, so it self-scales.
        "bb_width_pctile": _last(bb_width.rolling(120).rank(pct=True)),
        "ret_1": _last(c.pct_change()),
        "ret_3": _last(c.pct_change(3)),
        "funding_rate": funding_rate,
    }
    if taker_buy is not None:
        snap["taker_buy_ratio"] = _last(taker_buy / vol.replace(0, np.nan))
    else:
        snap["taker_buy_ratio"] = None
    if snap["vwap"] and snap["atr"]:
        snap["vwap_dist_atr"] = round((snap["close"] - snap["vwap"]) / snap["atr"], 2)
    else:
        snap["vwap_dist_atr"] = None
    return snap


# ---------------- individual detectors ----------------

def orb(df, snap, ctx):
    tf = ctx["tf"]
    if tf not in ("1m", "5m", "15m"):
        return None
    orr = ind.opening_range(df, minutes=30)
    hi, lo = orr["or_high"].iloc[-1], orr["or_low"].iloc[-1]
    if pd.isna(hi) or pd.isna(lo):
        return None
    c, prev = df["close"].iloc[-1], df["close"].iloc[-2]
    if prev <= hi < c:
        return {"setup": "orb", "direction": "long", "strength": 0.7,
                "detail": f"closed above 30-min opening range high {hi:,.0f}"}
    if prev >= lo > c:
        return {"setup": "orb", "direction": "short", "strength": 0.7,
                "detail": f"closed below 30-min opening range low {lo:,.0f}"}
    return None


def vwap_fade(df, snap, ctx):
    if ctx["tf"] not in INTRADAY_TFS:
        return None
    bands = ind.session_vwap_bands(df, 2.0)
    up, dn = bands["upper"].iloc[-1], bands["lower"].iloc[-1]
    c = snap["close"]
    if pd.notna(up) and c > up:
        return {"setup": "vwap_fade", "direction": "short", "strength": 0.6,
                "detail": f"price {c:,.0f} stretched above VWAP+2σ ({up:,.0f})"}
    if pd.notna(dn) and c < dn:
        return {"setup": "vwap_fade", "direction": "long", "strength": 0.6,
                "detail": f"price {c:,.0f} stretched below VWAP-2σ ({dn:,.0f})"}
    return None


def ema_pullback(df, snap, ctx):
    c, e20, e200 = snap["close"], snap["ema20"], snap["ema200"]
    if None in (c, e20, e200):
        return None
    low, high = df["low"].iloc[-1], df["high"].iloc[-1]
    if c > e200 and low <= e20 < c:
        return {"setup": "ema_pullback", "direction": "long", "strength": 0.65,
                "detail": "uptrend (above EMA200), pulled back to EMA20 and held"}
    if c < e200 and high >= e20 > c:
        return {"setup": "ema_pullback", "direction": "short", "strength": 0.65,
                "detail": "downtrend (below EMA200), rallied to EMA20 and rejected"}
    return None


def rsi2_extreme(df, snap, ctx):
    r = snap["rsi_2"]
    if r is None:
        return None
    # On a series with no movement RSI is undefined and the implementation pins
    # it to an extreme, which made this fire "deeply oversold" on a dead flat
    # tape. Require that price actually moved over the lookback.
    recent = df["close"].tail(10)
    if float(recent.max() - recent.min()) <= 0:
        return None
    if r < 5:
        return {"setup": "rsi2_extreme", "direction": "long", "strength": 0.5,
                "detail": f"RSI(2) = {r:.1f}, deeply oversold"}
    if r > 95:
        return {"setup": "rsi2_extreme", "direction": "short", "strength": 0.5,
                "detail": f"RSI(2) = {r:.1f}, deeply overbought"}
    return None


def funding_extreme(df, snap, ctx):
    rate = snap["funding_rate"]
    hist = ctx.get("funding_history") or []
    if rate is None or len(hist) < 60:
        return None
    hi = float(np.quantile(hist, 0.90))
    lo = float(np.quantile(hist, 0.10))
    if rate > hi and rate > 0:
        return {"setup": "funding_extreme", "direction": "short", "strength": 0.7,
                "detail": f"funding {rate:+.4%} above 90th percentile ({hi:+.4%}) — crowded longs"}
    if rate < lo and rate < 0:
        return {"setup": "funding_extreme", "direction": "long", "strength": 0.7,
                "detail": f"funding {rate:+.4%} below 10th percentile ({lo:+.4%}) — crowded shorts"}
    return None


def liquidation_cascade(df, snap, ctx):
    liqs = ctx.get("liquidations") or []
    if len(liqs) < 5:
        return None
    total = sum(x[2] for x in liqs)
    if total < ctx.get("liq_notional_threshold", 2_000_000):
        return None
    sells = sum(x[2] for x in liqs if x[1] == "SELL")
    direction = "long" if sells > total / 2 else "short"  # longs liquidated -> flush down
    return {"setup": "liquidation_cascade", "direction": direction,
            "strength": 0.8,
            "detail": f"{len(liqs)} liquidations, ${total/1e6:.1f}M notional in 60s"}


def volume_spike(df, snap, ctx):
    z = snap["vol_z"]
    if z is None or z < 3:
        return None
    # A spike with no price change carries no direction. The old expression
    # (`"long" if ret_1 and ret_1 > 0 else "short"`) reported *short* for both a
    # missing return and a flat bar, inventing a bearish signal out of nothing.
    ret = snap.get("ret_1")
    if ret is None or not np.isfinite(ret) or ret == 0:
        return None
    pct = df["volume"].iloc[-1] / max(df["volume"].rolling(48).mean().iloc[-1], 1e-9)
    direction = "long" if ret > 0 else "short"
    return {"setup": "volume_spike", "direction": direction,
            "strength": min(1.0, z / 6),
            "detail": f"volume {pct:.0f}x the 48-bar average (z={z:.1f})"}


def session_open_volatility(df, snap, ctx):
    if ctx["tf"] not in ("1m", "5m", "15m"):
        return None
    ts = df.index[-1]
    minute = ts.hour * 60 + ts.minute
    sessions = {"UTC open": 0, "London open": 8 * 60, "NY open": 13 * 60 + 30}
    for name, start in sessions.items():
        if 0 <= minute - start < 30:
            rng = df["high"].iloc[-1] - df["low"].iloc[-1]
            if snap["atr"] and rng > 2 * snap["atr"]:
                return {"setup": "session_open_volatility", "direction": "neutral",
                        "strength": 0.5,
                        "detail": f"{name}: bar range {rng:,.0f} > 2x ATR"}
    return None


def _pivots(df, left=5, right=5):
    """Confirmed swing highs/lows (fractals). Confirmation lags by `right` bars."""
    h, l = df["high"], df["low"]
    ph = h.shift(right).where(
        (h.shift(right) == h.rolling(left + right + 1).max().shift(0)))
    pl = l.shift(right).where(
        (l.shift(right) == l.rolling(left + right + 1).min().shift(0)))
    return ph.dropna(), pl.dropna()


def market_structure_break(df, snap, ctx):
    ph, pl = _pivots(df)
    if ph.empty or pl.empty:
        return None
    c, prev = df["close"].iloc[-1], df["close"].iloc[-2]
    last_high, last_low = ph.iloc[-1], pl.iloc[-1]
    if prev <= last_high < c:
        return {"setup": "structure_break", "direction": "long", "strength": 0.7,
                "detail": f"closed above last swing high {last_high:,.0f} (bullish BOS)"}
    if prev >= last_low > c:
        return {"setup": "structure_break", "direction": "short", "strength": 0.7,
                "detail": f"closed below last swing low {last_low:,.0f} (bearish BOS)"}
    return None


def fair_value_gap(df, snap, ctx):
    if len(df) < 3:
        return None
    h2, l2 = df["high"].iloc[-3], df["low"].iloc[-3]
    h0, l0 = df["high"].iloc[-1], df["low"].iloc[-1]
    atr = snap["atr"] or 0
    if l0 > h2 and (l0 - h2) > 0.5 * atr:
        return {"setup": "fvg", "direction": "long", "strength": 0.55,
                "detail": f"bullish fair value gap {h2:,.0f} → {l0:,.0f}"}
    if h0 < l2 and (l2 - h0) > 0.5 * atr:
        return {"setup": "fvg", "direction": "short", "strength": 0.55,
                "detail": f"bearish fair value gap {h0:,.0f} → {l2:,.0f}"}
    return None


def order_block(df, snap, ctx):
    """Last opposite candle before a displacement move (> 2x ATR body)."""
    if len(df) < 3 or not snap["atr"]:
        return None
    body = df["close"].iloc[-1] - df["open"].iloc[-1]
    prev_body = df["close"].iloc[-2] - df["open"].iloc[-2]
    if body > 2 * snap["atr"] and prev_body < 0:
        return {"setup": "order_block", "direction": "long", "strength": 0.55,
                "detail": f"bullish displacement after down candle — demand zone "
                          f"{df['low'].iloc[-2]:,.0f}–{df['high'].iloc[-2]:,.0f}"}
    if body < -2 * snap["atr"] and prev_body > 0:
        return {"setup": "order_block", "direction": "short", "strength": 0.55,
                "detail": f"bearish displacement after up candle — supply zone "
                          f"{df['low'].iloc[-2]:,.0f}–{df['high'].iloc[-2]:,.0f}"}
    return None


def support_resistance(df, snap, ctx):
    ph, pl = _pivots(df)
    if not snap["atr"]:
        return None
    c = snap["close"]
    lo_bar, hi_bar = df["low"].iloc[-1], df["high"].iloc[-1]
    tol = 0.5 * snap["atr"]
    for lvl in pl.iloc[-5:]:
        if abs(lo_bar - lvl) < tol and c > lvl + tol * 0.5:
            return {"setup": "sr_reaction", "direction": "long", "strength": 0.55,
                    "detail": f"rejection wick off support {lvl:,.0f}"}
    for lvl in ph.iloc[-5:]:
        if abs(hi_bar - lvl) < tol and c < lvl - tol * 0.5:
            return {"setup": "sr_reaction", "direction": "short", "strength": 0.55,
                    "detail": f"rejection wick off resistance {lvl:,.0f}"}
    return None


def trend_continuation(df, snap, ctx):
    e20, e50, e200, c = snap["ema20"], snap["ema50"], snap["ema200"], snap["close"]
    if None in (e20, e50, e200, c):
        return None
    prev_high, prev_low = df["high"].iloc[-2], df["low"].iloc[-2]
    if e20 > e50 > e200 and df["low"].iloc[-2] <= e20 and c > prev_high:
        return {"setup": "trend_continuation", "direction": "long", "strength": 0.6,
                "detail": "stacked bullish EMAs, pullback resolved upward"}
    if e20 < e50 < e200 and df["high"].iloc[-2] >= e20 and c < prev_low:
        return {"setup": "trend_continuation", "direction": "short", "strength": 0.6,
                "detail": "stacked bearish EMAs, pullback resolved downward"}
    return None


def mean_reversion(df, snap, ctx):
    z = snap["bb_z"]
    if z is None or len(df) < 21:
        return None
    c = df["close"]
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    prev_z = (c.iloc[-2] - mid.iloc[-2]) / std.iloc[-2] if std.iloc[-2] else 0
    if prev_z < -2 and z > -2:
        return {"setup": "mean_reversion", "direction": "long", "strength": 0.5,
                "detail": f"closed back inside lower Bollinger band (z {prev_z:.1f} → {z:.1f})"}
    if prev_z > 2 and z < 2:
        return {"setup": "mean_reversion", "direction": "short", "strength": 0.5,
                "detail": f"closed back inside upper Bollinger band (z {prev_z:.1f} → {z:.1f})"}
    return None


# ---------------- retail scalping playbook ----------------
# The setups most 1-5m crypto scalpers actually run. Same contract as above:
# they flag that the pattern printed, nothing more.

def ema_cross_pullback(df, snap, ctx):
    """EMA 9/21 regime + entry on the pullback, not the chase.

    The cross alone is late and whipsaws; the tradable part is the first
    retest of the fast EMA while the cross is still fresh.
    """
    e9, e21, c = snap["ema9"], snap["ema21"], snap["close"]
    if None in (e9, e21, c) or len(df) < 30:
        return None
    f = ind.ema(df["close"], 9)
    s = ind.ema(df["close"], 21)
    diff = (f - s).to_numpy()
    sign = np.sign(diff)
    # bars since the last sign flip = how fresh the regime is
    flips = np.where(np.diff(sign) != 0)[0]
    since = len(diff) - 1 - flips[-1] if len(flips) else 999
    if since > 20:
        return None
    lo, hi = df["low"].iloc[-1], df["high"].iloc[-1]
    band_lo, band_hi = min(e9, e21), max(e9, e21)
    strength = 0.7 - 0.015 * since
    if e9 > e21 and lo <= band_hi and c > e9:
        return {"setup": "ema_cross_pullback", "direction": "long",
                "strength": round(max(0.4, strength), 2),
                "detail": f"EMA9>EMA21 for {since} bars, price pulled into the "
                          f"{band_lo:,.0f}–{band_hi:,.0f} EMA band and closed back above EMA9"}
    if e9 < e21 and hi >= band_lo and c < e9:
        return {"setup": "ema_cross_pullback", "direction": "short",
                "strength": round(max(0.4, strength), 2),
                "detail": f"EMA9<EMA21 for {since} bars, price rallied into the "
                          f"{band_lo:,.0f}–{band_hi:,.0f} EMA band and closed back below EMA9"}
    return None


def vwap_bounce(df, snap, ctx):
    """VWAP as the intraday line in the sand: trade the rejection off it.

    Opposite of vwap_fade — that one fades the 2σ stretch, this one buys the
    return to the mean while price stays on its side of VWAP.
    """
    if ctx["tf"] not in INTRADAY_TFS:
        return None
    v, c, a = snap["vwap"], snap["close"], snap["atr"]
    if None in (v, c, a) or a <= 0:
        return None
    lo, hi, o = df["low"].iloc[-1], df["high"].iloc[-1], df["open"].iloc[-1]
    tol = 0.30 * a
    if c > v and lo <= v + tol and c > o:
        return {"setup": "vwap_bounce", "direction": "long", "strength": 0.6,
                "detail": f"held above session VWAP {v:,.0f}: wick tagged it and "
                          f"closed green {c - v:,.0f} above"}
    if c < v and hi >= v - tol and c < o:
        return {"setup": "vwap_bounce", "direction": "short", "strength": 0.6,
                "detail": f"rejected at session VWAP {v:,.0f}: wick tagged it and "
                          f"closed red {v - c:,.0f} below"}
    return None


def liquidity_grab(df, snap, ctx):
    """Stop hunt: an obvious swing gets swept, then the close rejects it.

    Deep BTC/ETH books make this the most reliable scalp pattern — the sweep
    is where the resting stops sat, and the reversal is the fill.
    """
    a = snap["atr"]
    if not a or len(df) < 25:
        return None
    ph, pl = _pivots(df, 4, 4)
    if ph.empty or pl.empty:
        return None
    hi, lo, c = df["high"].iloc[-1], df["low"].iloc[-1], df["close"].iloc[-1]
    swing_hi = float(ph.iloc[-1])
    swing_lo = float(pl.iloc[-1])
    up_wick = hi - max(c, df["open"].iloc[-1])
    dn_wick = min(c, df["open"].iloc[-1]) - lo
    if hi > swing_hi and c < swing_hi and up_wick > 0.45 * a:
        return {"setup": "liquidity_grab", "direction": "short",
                "strength": round(min(0.85, 0.55 + up_wick / a * 0.2), 2),
                "detail": f"swept swing high {swing_hi:,.0f} then closed back below "
                          f"({up_wick / a:.1f}x ATR upper wick) — buy stops taken"}
    if lo < swing_lo and c > swing_lo and dn_wick > 0.45 * a:
        return {"setup": "liquidity_grab", "direction": "long",
                "strength": round(min(0.85, 0.55 + dn_wick / a * 0.2), 2),
                "detail": f"swept swing low {swing_lo:,.0f} then closed back above "
                          f"({dn_wick / a:.1f}x ATR lower wick) — sell stops taken"}
    return None


def rsi_sr_confluence(df, snap, ctx):
    """RSI is noise on its own; at a pivot level it is a location filter."""
    r, a, c = snap["rsi_14"], snap["atr"], snap["close"]
    if None in (r, a, c) or a <= 0:
        return None
    ph, pl = _pivots(df, 4, 4)
    lo, hi = df["low"].iloc[-1], df["high"].iloc[-1]
    tol = 0.45 * a
    if r < 36 and not pl.empty:
        for lvl in pl.iloc[-6:]:
            if abs(lo - float(lvl)) < tol:
                return {"setup": "rsi_sr_confluence", "direction": "long",
                        "strength": round(0.5 + (36 - r) / 100, 2),
                        "detail": f"RSI(14)={r:.0f} oversold *at* prior support "
                                  f"{float(lvl):,.0f} — location plus momentum, not RSI alone"}
    if r > 64 and not ph.empty:
        for lvl in ph.iloc[-6:]:
            if abs(hi - float(lvl)) < tol:
                return {"setup": "rsi_sr_confluence", "direction": "short",
                        "strength": round(0.5 + (r - 64) / 100, 2),
                        "detail": f"RSI(14)={r:.0f} overbought *at* prior resistance "
                                  f"{float(lvl):,.0f} — location plus momentum, not RSI alone"}
    return None


def breakout_retest(df, snap, ctx):
    """Skip the naked break, take the retest — fakeouts die on the retest."""
    a = snap["atr"]
    if not a or len(df) < 40:
        return None
    ph, pl = _pivots(df, 4, 4)
    close = df["close"]
    c, prev = float(close.iloc[-1]), float(close.iloc[-2])
    lo, hi = df["low"].iloc[-1], df["high"].iloc[-1]
    tol = 0.35 * a
    look = 20

    for lvl in [float(x) for x in ph.iloc[-8:]]:
        broke = ((close.iloc[-look:-2] > lvl + 0.2 * a).any()
                 and (close.iloc[-look - 6:-look + 2] < lvl).any())
        if broke and lo <= lvl + tol and c > lvl and c > prev:
            return {"setup": "breakout_retest", "direction": "long", "strength": 0.68,
                    "detail": f"broke resistance {lvl:,.0f} earlier, came back to retest "
                              f"it as support and held"}
    for lvl in [float(x) for x in pl.iloc[-8:]]:
        broke = ((close.iloc[-look:-2] < lvl - 0.2 * a).any()
                 and (close.iloc[-look - 6:-look + 2] > lvl).any())
        if broke and hi >= lvl - tol and c < lvl and c < prev:
            return {"setup": "breakout_retest", "direction": "short", "strength": 0.68,
                    "detail": f"broke support {lvl:,.0f} earlier, rallied back to retest "
                              f"it as resistance and failed"}
    return None


def bb_squeeze(df, snap, ctx):
    """Volatility contraction then expansion. The squeeze is the setup, the
    expansion bar is the trigger — direction comes from the break, not a guess."""
    pct = snap["bb_width_pctile"]
    # NaN has to be rejected explicitly. Every comparison against NaN is False,
    # so a NaN percentile slipped past both the squeeze and the expansion test
    # below and the detector fired on any close outside the prior band.
    if pct is None or not np.isfinite(pct) or len(df) < 130:
        return None
    c = df["close"]
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    width = (4 * std) / mid
    prev_raw = width.rolling(120).rank(pct=True).iloc[-2]
    if pd.isna(prev_raw):
        return None
    prev_pct = float(prev_raw)
    if prev_pct > 0.22:                     # was not actually squeezed
        return None
    if pct <= prev_pct * 1.25:              # not expanding yet
        return None
    up = float((mid + 2 * std).iloc[-2])
    dn = float((mid - 2 * std).iloc[-2])
    last = float(c.iloc[-1])
    if last > up:
        return {"setup": "bb_squeeze", "direction": "long", "strength": 0.62,
                "detail": f"volatility squeeze (band width in the {prev_pct:.0%} "
                          f"percentile) broke upward through {up:,.0f}"}
    if last < dn:
        return {"setup": "bb_squeeze", "direction": "short", "strength": 0.62,
                "detail": f"volatility squeeze (band width in the {prev_pct:.0%} "
                          f"percentile) broke downward through {dn:,.0f}"}
    return None


DETECTORS = {
    "orb": orb,
    "ema_cross_pullback": ema_cross_pullback,
    "vwap_bounce": vwap_bounce,
    "liquidity_grab": liquidity_grab,
    "rsi_sr_confluence": rsi_sr_confluence,
    "breakout_retest": breakout_retest,
    "bb_squeeze": bb_squeeze,
    "vwap_fade": vwap_fade,
    "ema_pullback": ema_pullback,
    "rsi2_extreme": rsi2_extreme,
    "funding_extreme": funding_extreme,
    "liquidation_cascade": liquidation_cascade,
    "volume_spike": volume_spike,
    "session_open_volatility": session_open_volatility,
    "structure_break": market_structure_break,
    "fvg": fair_value_gap,
    "order_block": order_block,
    "sr_reaction": support_resistance,
    "trend_continuation": trend_continuation,
    "mean_reversion": mean_reversion,
}

SETUP_CATEGORIES = {
    "breakout": ["orb", "structure_break", "session_open_volatility",
                 "breakout_retest", "bb_squeeze"],
    "reversion": ["vwap_fade", "rsi2_extreme", "mean_reversion", "sr_reaction",
                  "vwap_bounce", "rsi_sr_confluence"],
    "trend": ["ema_pullback", "trend_continuation", "ema_cross_pullback"],
    "flow": ["funding_extreme", "liquidation_cascade", "volume_spike"],
    "smc": ["fvg", "order_block", "liquidity_grab"],
}

# The subset most 1-5m scalpers actually trade. Surfaced separately in the UI
# so the playbook is one click away from the full detector list.
SCALP_PLAYBOOK = ["ema_cross_pullback", "vwap_bounce", "liquidity_grab",
                  "rsi_sr_confluence", "breakout_retest", "bb_squeeze"]


_detector_errors: dict[str, dict] = {}


def run_detectors(df: pd.DataFrame, snap: dict, ctx: dict,
                  enabled: set[str] | None = None) -> list[dict]:
    events = []
    for name, fn in DETECTORS.items():
        if enabled is not None and name not in enabled:
            continue
        try:
            ev = fn(df, snap, ctx)
            if ev:
                events.append(ev)
        except Exception as e:
            # Failures used to be swallowed silently, which meant a detector
            # broken by a data edge case would stay permanently dead and look
            # exactly like one that simply never triggers. Recorded and logged
            # once per distinct error so it is visible without spamming.
            slot = _detector_errors.setdefault(name, {"count": 0, "last": None})
            slot["count"] += 1
            msg = f"{type(e).__name__}: {e}"
            if slot["last"] != msg:
                slot["last"] = msg
                log.warning("detector %s failed (%dx): %s", name, slot["count"], msg)
    return events


def detector_errors() -> dict:
    """Detectors that have thrown, for the diagnostics endpoint."""
    return {k: v for k, v in _detector_errors.items() if v["count"]}
