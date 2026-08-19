"""Directional scoring (1-100) plus a mechanical trade plan.

Nine weighted components each return a signed reading in [-1, +1] where
positive is bullish. Their weighted mean is the net direction; the headline
score folds in how many components agree and a quality term that punishes
chop, extreme volatility and conflicting signals.

Everything is derived from live candles and the dynamic zones — no fixed
thresholds on price, no hard-coded levels. The leverage block is arithmetic
on the user's own position size, not a recommendation.

This measures agreement between conditions. It is not a probability, not an
expected return and not financial advice.
"""

from __future__ import annotations

import math

from sentinel import confluence, zones as zmod

WEIGHTS = {
    "mtf_trend": 18,
    "setups": 18,
    "structure": 15,
    "trend_local": 12,
    "flow": 12,
    "momentum": 10,
    "vwap": 9,
    "funding": 8,
}

# volatility is handled as a damping multiplier, not a directional vote

# Gate defaults, measured rather than chosen. threshold_sweep.py replays this
# exact engine over ~1400 bars of 5m and 15m on BTC and ETH (817 filled trades),
# walks each plan forward to stop-or-target, and charges an 0.08% round trip.
#
# What the replay actually showed:
#   baseline (no gate)                     817 trades, avg -0.404 R
#   min_score 50                           308 trades, avg -0.267 R   improves 4/4 runs
#   min_score 50 + min_rr 1.8 + no squeeze 229 trades, avg -0.189 R   improves 4/4 runs
#
# Rejected despite looking good pooled:
#   min_score 65 scored best in the pooled table (-0.054 R) but improved in only
#     1 of 2 runs with enough samples, and was -0.864 R on ETH 15m. Curve fit.
#   min_agreement had no consistent sign — pooled it helped, on ETH 15m the
#     highest-agreement bucket was the *worst*. Left ungated on purpose.
#   Requiring a wide stop (cost <= 0.2 R) made things worse, not better: tight
#     stops carry positive gross edge and lose it all to cost, while wide stops
#     have no gross edge to begin with. There is no stop distance that nets out.
#
# None of these are profitable. The gate cuts the loss roughly in half and
# removes ~72% of trades; it does not create an edge. See GATE_DOC.
DEFAULT_GATE = {
    "min_score": 50,        # headline conviction — improved in 4/4 runs
    "min_net": 0.12,        # directional lean; no consistent signal, left as-is
    "min_agreement": 0.0,   # deliberately ungated, sign was inconsistent
    "min_rr": 1.8,          # reward:risk to first target — part of the 4/4 combo
    "min_timing": 0,        # untestable in replay (no historical session profile)
    "block_squeeze": True,  # squeeze was worst regime in all 4 runs
}


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _fmt(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{nd}f}"


# ------------------------------------------------------------ components

def c_mtf_trend(tf: str, snaps: dict) -> dict:
    """Trend on every timeframe above the current one, weighted by timeframe."""
    rank = zmod.tf_rank(tf)
    votes, total_w, parts, dirs = 0.0, 0.0, [], []
    for t, s in sorted(snaps.items(), key=lambda kv: zmod.tf_rank(kv[0])):
        if zmod.tf_rank(t) < rank or not s:
            continue
        c, e50, e200 = s.get("close"), s.get("ema50"), s.get("ema200")
        e9, e21 = s.get("ema9"), s.get("ema21")
        if None in (c, e200):
            continue
        v = 0.0
        v += 0.5 if c > e200 else -0.5
        if e50 is not None:
            v += 0.2 if c > e50 else -0.2
        if None not in (e9, e21):
            v += 0.3 if e9 > e21 else -0.3
        w = zmod.TF_WEIGHT.get(t, 0.5) * (1.4 if t != tf else 1.0)
        votes += _clip(v) * w
        total_w += w
        label = "up" if v > 0 else "down" if v < 0 else "flat"
        parts.append(f"{t} {label}")
        dirs.append(label)
    if not total_w:
        return {"value": 0.0, "detail": "not enough higher-timeframe history yet"}
    val = _clip(votes / total_w)
    lead = "up" if val > 0 else "down"
    same = sum(1 for d in dirs if d == lead)
    if same == len(dirs):
        head = f"every timeframe {lead}"
    elif same > len(dirs) / 2:
        odd = [p for p, d in zip(parts, dirs) if d != lead]
        head = f"mostly {lead} ({', '.join(odd)} against)"
    else:
        head = "split, no dominant higher-timeframe direction"
    return {"value": val,
            "detail": f"{head} — {', '.join(parts)}",
            "extra": {"per_tf": parts, "agree": f"{same}/{len(dirs)}"}}


def c_trend_local(snap: dict) -> dict:
    c, e9, e21 = snap.get("close"), snap.get("ema9"), snap.get("ema21")
    e50, e200 = snap.get("ema50"), snap.get("ema200")
    if None in (c, e9, e21, e50, e200):
        return {"value": 0.0, "detail": "EMA history incomplete"}
    up = [e9 > e21, e21 > e50, e50 > e200, c > e9]
    score = (sum(up) - 2) / 2                     # 4 true -> +1, 0 true -> -1
    if e9 > e21 > e50 > e200:
        label = "fully stacked bullish (9>21>50>200)"
    elif e9 < e21 < e50 < e200:
        label = "fully stacked bearish (9<21<50<200)"
    else:
        label = "EMAs interleaved — ranging, not trending"
        score *= 0.6
    return {"value": _clip(score), "detail": label}


def c_momentum(snap: dict) -> dict:
    r, roc = snap.get("rsi_14"), snap.get("ret_3")
    if r is None:
        return {"value": 0.0, "detail": "no RSI yet"}
    v = _clip((r - 50) / 30) * 0.7
    if roc is not None:
        v += _clip(roc * 60) * 0.3
    return {"value": _clip(v),
            "detail": f"RSI(14) {r:.0f}"
                      + (f", 3-bar change {roc * 100:+.2f}%" if roc is not None else "")}


def c_structure(zp: dict, snap: dict) -> dict:
    price, atr = zp.get("price"), zp.get("atr")
    sup, res = zp.get("nearest_support"), zp.get("nearest_resistance")
    inside = zp.get("inside")
    if not price or not atr:
        return {"value": 0.0, "detail": "no zones built yet"}
    bits, v = [], 0.0
    d_sup = (price - sup["hi"]) / atr if sup else None
    d_res = (res["lo"] - price) / atr if res else None
    if d_sup is not None and d_res is not None:
        # more headroom above than risk below is bullish location, and vice versa
        room = (d_res - d_sup) / max(d_res + d_sup, 0.4)
        v += _clip(room) * 0.55
        bits.append(f"{d_sup:.1f}x ATR above {sup['tier']} support "
                    f"{sup['lo']:,.0f}–{sup['hi']:,.0f}")
        bits.append(f"{d_res:.1f}x ATR below {res['tier']} resistance "
                    f"{res['lo']:,.0f}–{res['hi']:,.0f}")
        if d_res < 0.7 and res["strength"] > 0.55:
            v -= 0.35
            bits.append("pressed right under a strong ceiling")
        if d_sup < 0.7 and sup["strength"] > 0.55:
            v += 0.35
            bits.append("sitting right on a strong floor")
    elif d_res is not None:
        v -= 0.3
        bits.append(f"resistance {d_res:.1f}x ATR above, no mapped support below")
    elif d_sup is not None:
        v += 0.3
        bits.append(f"support {d_sup:.1f}x ATR below, clear air above")
    if inside:
        v *= 0.5
        bits.append(f"price is inside the {inside['tier']} zone "
                    f"{inside['lo']:,.0f}–{inside['hi']:,.0f} — wait for a side")
    flips = [z for z in zp.get("zones", []) if z["broken"]
             and abs(z["distance_atr"]) < 1.5]
    for z in flips:
        bits.append(f"recently broken {'/'.join(z['tfs'])} level at {z['mid']:,.0f} "
                    f"now acting as {z['role']}")
    return {"value": _clip(v), "detail": "; ".join(bits) or "no nearby zones"}


def c_flow(snap: dict, ctx: dict) -> dict:
    v, bits = 0.0, []
    vz, ret = snap.get("vol_z"), snap.get("ret_1")
    if vz is not None and ret is not None:
        conf = _clip(vz / 3) * (1 if ret > 0 else -1)
        v += conf * 0.45
        bits.append(f"volume z={vz:+.1f} on a {ret * 100:+.2f}% bar")
    tbr = snap.get("taker_buy_ratio")
    if tbr is not None:
        v += _clip((tbr - 0.5) * 6) * 0.35
        bits.append(f"taker buys {tbr * 100:.0f}% of volume")
    liqs = ctx.get("liquidations") or []
    if liqs:
        total = sum(x[2] for x in liqs)
        sells = sum(x[2] for x in liqs if x[1] == "SELL")
        skew = (sells / total * 2 - 1) if total else 0
        # longs being liquidated (SELL side) flushes down, then snaps back up
        v += _clip(skew) * 0.20
        bits.append(f"${total / 1e6:.1f}M liquidated in 60s, "
                    f"{'longs' if skew > 0 else 'shorts'} taking it")
    if not bits:
        return {"value": 0.0, "detail": "no flow data"}
    return {"value": _clip(v), "detail": "; ".join(bits)}


def c_funding(snap: dict, ctx: dict) -> dict:
    fr = snap.get("funding_rate")
    if fr is None:
        return {"value": 0.0, "detail": "funding unavailable"}
    hist = ctx.get("funding_history") or []
    pct = None
    if len(hist) > 30:
        pct = sum(1 for x in hist if x < fr) / len(hist)
    # contrarian: crowded longs (positive funding) is bearish fuel
    v = _clip(-fr * 2000)
    if pct is not None:
        v = _clip(v * (0.6 + 0.8 * abs(pct - 0.5) * 2))
    d = f"funding {fr:+.4%}"
    if pct is not None:
        d += f" ({pct:.0%} percentile of 90 days) — crowd is " \
             f"{'long' if fr > 0 else 'short'}"
    return {"value": v, "detail": d}


# How far from VWAP counts as "stretched", in ATR of that timeframe. A 1m ATR
# is a tiny fraction of a session's range, so 1m price sits many 1m-ATRs from
# VWAP as a matter of course; using one threshold for every timeframe would
# flag 1m as stretched almost permanently.
VWAP_STRETCH_ATR = {"1m": 5.0, "5m": 3.0, "15m": 2.2, "1h": 1.6,
                    "4h": 1.3, "1d": 1.2}


def c_vwap(snap: dict, tf: str) -> dict:
    d = snap.get("vwap_dist_atr")
    if d is None:
        return {"value": 0.0, "detail": "VWAP not established this session"}
    lim = VWAP_STRETCH_ATR.get(tf, 2.0)
    if abs(d) <= lim:
        # inside the normal band: being on a side of VWAP endorses that side
        v = _clip(d / lim) * 0.8
        note = f"within the normal {lim:.1f}x ATR band for {tf} — trend side, not stretched"
    else:
        # past the threshold the read inverts: the stretch itself is the signal
        v = -math.copysign(_clip((abs(d) - lim) / (lim * 0.8)), d)
        note = (f"beyond the {lim:.1f}x ATR band normal for {tf} — reversion risk "
                f"now outweighs the trend")
    return {"value": v, "detail": f"{d:+.1f}x ATR from session VWAP, {note}"}


def c_setups(events: list[dict]) -> dict:
    if not events:
        return {"value": 0.0, "detail": "no setup fired on this bar",
                "extra": {"events": []}}
    tot_w, signed = 0.0, 0.0
    for e in events:
        w = confluence.SETUP_WEIGHTS.get(e["setup"], 8) * e.get("strength", 0.5)
        tot_w += w
        if e["direction"] == "long":
            signed += w
        elif e["direction"] == "short":
            signed -= w
    v = _clip(signed / tot_w) if tot_w else 0.0
    longs = sum(1 for e in events if e["direction"] == "long")
    shorts = sum(1 for e in events if e["direction"] == "short")
    if longs and shorts:
        v *= 0.6
    return {"value": v,
            "detail": ", ".join(f"{e['setup'].replace('_', ' ')} "
                                f"({e['direction']})" for e in events),
            "extra": {"events": events, "longs": longs, "shorts": shorts}}


# Round-trip cost of one scalp on a crypto CFD: spread plus commission, as a
# fraction of price. Deliberately conservative — the point is the comparison,
# not a quote.
ROUND_TRIP_COST = 0.0008


def c_volatility(snap: dict) -> dict:
    """Non-directional. Returns a damping factor, a regime label and a cost read.

    Absolute ATR% and band-width percentile answer different questions, so they
    are reported separately: percentile says whether volatility is high *for
    this market right now*, ATR% says whether a one-ATR move even covers the
    cost of trading it.
    """
    ap, bw = snap.get("atr_pct"), snap.get("bb_width_pctile")
    damp, bits = 1.0, []
    cost_ratio = None

    # regime comes from the self-scaling percentile, not a fixed ATR threshold
    regime = "normal"
    if bw is not None:
        bits.append(f"range width in the {bw:.0%} percentile of its own history")
        if bw < 0.22:
            regime = "squeeze"
            damp *= 0.80
            bits.append("compressed — an expansion is pending and the direction "
                        "is not yet decided")
        elif bw > 0.82:
            regime = "expansion"
            damp *= 0.92
            bits.append("already expanded — late in the move, chasing is expensive")

    if ap is not None:
        cost_ratio = ap / ROUND_TRIP_COST
        bits.append(f"one ATR is {ap * 100:.2f}% of price "
                    f"({cost_ratio:.1f}x a ~{ROUND_TRIP_COST * 100:.2f}% round-trip cost)")
        if ap > 0.020:
            damp *= 0.72
            bits.append("absolute volatility is extreme — every reading here is "
                        "fragile and stops need real width")
        elif ap > 0.012:
            damp *= 0.88
        if cost_ratio < 2.0:
            damp *= 0.85
            bits.append("a one-ATR winner barely clears fees and spread — the "
                        "edge has to come from a multi-ATR target, not a scalp")

    return {"damp": round(damp, 3), "regime": regime, "atr_pct": ap,
            "cost_ratio": round(cost_ratio, 2) if cost_ratio else None,
            "detail": "; ".join(bits) or "no volatility data"}


# ------------------------------------------------------------ trade plan

def _plan(net: float, action: str, zp: dict, snap: dict) -> dict:
    price, atr = zp.get("price"), zp.get("atr")
    if not price or not atr:
        return {}
    sup, res = zp.get("nearest_support"), zp.get("nearest_resistance")
    # stop anchors are width-filtered; a range-sized band cannot hold a stop
    ssup, sres = zp.get("stop_support"), zp.get("stop_resistance")
    inside = zp.get("inside")
    plan = {"reference_price": price, "atr": atr}

    # A zone can only anchor the entry if it is close enough to trade from now.
    # Without this gate a level 2% away becomes the "entry", and every number
    # downstream — stop, targets, risk — describes a trade at a price the market
    # is nowhere near.
    ANCHOR_REACH = 1.5 * atr

    if action == "buy":
        anchor = (ssup if ssup and 0 <= price - ssup["hi"] <= ANCHOR_REACH else None)
        entry = anchor["hi"] if anchor else price
        stop = (anchor["lo"] - 0.3 * atr) if anchor else price - 1.2 * atr
        # when price is inside a band, the band's own ceiling is the next real
        # obstacle — the resistance beyond it is not the first thing price meets
        if inside and inside["hi"] > price:
            level = inside["hi"]
        elif res and res["lo"] > price:
            level = res["lo"]
        else:
            level = price + 1.5 * atr
        plan["side"] = "long"
    elif action == "sell":
        anchor = (sres if sres and 0 <= sres["lo"] - price <= ANCHOR_REACH else None)
        entry = anchor["lo"] if anchor else price
        stop = (anchor["hi"] + 0.3 * atr) if anchor else price + 1.2 * atr
        if inside and inside["lo"] < price:
            level = inside["lo"]
        elif sup and sup["hi"] < price:
            level = sup["hi"]
        else:
            level = price - 1.5 * atr
        plan["side"] = "short"
    else:
        plan["side"] = "flat"
        plan["entry"] = None
        plan["stop"] = None
        plan["targets"] = []
        plan["note"] = ("No plan while the score says wait. The levels below are "
                        "what to watch for a trigger.")
        # When price is inside a band, that band's own edges are the triggers —
        # the next zone beyond it is not what price has to clear first.
        above = (inside["hi"] if inside and inside["hi"] > price
                 else res["hi"] if res else None)
        below = (inside["lo"] if inside and inside["lo"] < price
                 else sup["lo"] if sup else None)
        plan["watch"] = {"long_above": above, "short_below": below}
        return plan

    # a stop more than 2.5 ATR from entry is not a scalp stop; fall back to ATR
    if abs(entry - stop) > 2.5 * atr:
        stop = entry - 1.2 * atr if action == "buy" else entry + 1.2 * atr
        plan["stop_source"] = "1.2x ATR (the zone behind was too wide to use)"
    else:
        plan["stop_source"] = (
            f"0.3x ATR beyond the {anchor['tier']} zone "
            f"{anchor['lo']:,.2f}–{anchor['hi']:,.2f}" if anchor
            else "1.2x ATR — no zone sits within 1.5x ATR of price to anchor against")

    plan["entry_type"] = ("limit — wait for the pullback into the zone"
                          if abs(entry - price) > 0.05 * atr else "market at close")
    risk = abs(entry - stop)
    sign = 1 if action == "buy" else -1
    plan["entry"] = round(entry, 2)
    plan["stop"] = round(stop, 2)
    plan["anchor_zone"] = anchor
    plan["risk"] = round(risk, 2)
    plan["risk_pct"] = round(risk / entry * 100, 3)

    # T1 is whichever comes first: the next structural level, or 2R. A tight
    # stop against a distant level otherwise produces a 13R "target" that no
    # scalp on this timeframe will ever reach.
    r2 = entry + sign * 2 * risk
    level_r = abs(level - entry) / risk if risk else 0
    if level_r <= 2:
        t1, t1_label = level, "T1 (next level)"
        t2, t2_label = r2, "T2 (2R)"
    else:
        t1, t1_label = r2, "T1 (2R — the level is further out)"
        t2, t2_label = level, f"T2 (next level, {level_r:.1f}R)"

    plan["structural_level"] = round(level, 2)
    plan["structural_r"] = round(level_r, 2)
    plan["targets"] = [
        {"label": t1_label, "price": round(t1, 2),
         "r": round(abs(t1 - entry) / risk, 2) if risk else None},
        {"label": t2_label, "price": round(t2, 2),
         "r": round(abs(t2 - entry) / risk, 2) if risk else None},
    ]
    plan["rr"] = plan["targets"][0]["r"]
    plan["invalidation"] = (
        f"a close {'below' if action == 'buy' else 'above'} {plan['stop']:,.2f} "
        f"({plan['risk_pct']:.2f}% away). Beyond that the reason for the trade is gone."
    )
    return plan


def _leverage(plan: dict, snap: dict, qty: float, levs: tuple[int, ...]) -> dict:
    """Position arithmetic for the user's actual size and leverage band.

    Liquidation distance is approximated as 1/leverage of price — the point
    where the position's loss equals the posted margin. Real Exness stop-out
    triggers slightly earlier once spread, swap and commission are deducted,
    so treat these numbers as the optimistic bound.
    """
    price = plan.get("reference_price") or snap.get("close")
    if not price:
        return {}
    notional = qty * price
    atr_pct = (snap.get("atr_pct") or 0) * 100
    stop_pct = plan.get("risk_pct")

    rows = []
    for L in levs:
        liq_pct = 100.0 / L
        margin = notional / L
        row = {
            "leverage": L,
            "margin": round(margin, 2),
            "liq_move_pct": round(liq_pct, 3),
            "liq_price_long": round(price * (1 - liq_pct / 100), 2),
            "liq_price_short": round(price * (1 + liq_pct / 100), 2),
            "liq_in_atr": round(liq_pct / atr_pct, 2) if atr_pct else None,
        }
        if stop_pct:
            row["stop_fits"] = liq_pct > stop_pct * 1.5
            row["verdict"] = ("stop fits with room" if liq_pct > stop_pct * 2
                              else "stop barely fits" if liq_pct > stop_pct * 1.5
                              else "LIQUIDATED BEFORE YOUR STOP")
        rows.append(row)

    max_safe = None
    if stop_pct:
        max_safe = int(100.0 / (stop_pct * 2)) if stop_pct > 0 else None

    out = {
        "quantity": qty,
        "price": round(price, 2),
        "notional": round(notional, 2),
        "atr_pct": round(atr_pct, 3),
        "stop_pct": stop_pct,
        "rows": rows,
        "max_leverage_for_this_stop": max_safe,
    }
    if stop_pct and atr_pct:
        out["note"] = (
            f"This setup's stop is {stop_pct:.2f}% away. One ATR is {atr_pct:.2f}%. "
            f"To keep liquidation at least 2x further out than the stop you need "
            f"{max_safe}x or less. Anything above that liquidates you on ordinary "
            f"noise before the trade is proven wrong."
        )
    elif atr_pct:
        out["note"] = (
            f"One ATR is {atr_pct:.2f}% of price. At 400x liquidation sits "
            f"{100 / 400:.2f}% away — {(100 / 400) / atr_pct:.2f} ATR. A single "
            f"ordinary candle covers that distance."
        )
    return out


# ------------------------------------------------------------ main entry

def analyze(symbol: str, tf: str, frames: dict, snaps: dict, events: list[dict],
            zone_payload: dict, ctx: dict, *, qty: float = 0.01,
            leverages: tuple[int, ...] = (100, 200, 300, 400),
            gate: dict | None = None, timing_score: int | None = None) -> dict:
    snap = snaps.get(tf) or {}
    comps = {
        "mtf_trend": c_mtf_trend(tf, snaps),
        "trend_local": c_trend_local(snap),
        "momentum": c_momentum(snap),
        "structure": c_structure(zone_payload, snap),
        "flow": c_flow(snap, ctx),
        "funding": c_funding(snap, ctx),
        "vwap": c_vwap(snap, tf),
        "setups": c_setups(events),
    }
    vol = c_volatility(snap)

    num = sum(WEIGHTS[k] * comps[k]["value"] for k in comps)
    den = sum(WEIGHTS.values())
    net = _clip(num / den)

    signed = [comps[k]["value"] for k in comps if abs(comps[k]["value"]) > 0.08]
    if signed:
        agree = sum(1 for v in signed if (v > 0) == (net > 0)) / len(signed)
    else:
        agree = 0.0

    conflicts = sum(1 for k in comps
                    if abs(comps[k]["value"]) > 0.25
                    and (comps[k]["value"] > 0) != (net > 0))
    quality = _clip(1.0 - 0.12 * conflicts, 0.0, 1.0)
    if vol["regime"] == "squeeze":
        quality *= 0.85

    raw = abs(net)
    strength = (0.55 * raw + 0.30 * agree + 0.15 * quality) * vol["damp"]
    score = int(max(1, min(100, round(strength * 100))))

    buy_score = int(max(1, min(100, round(50 + 50 * net))))
    sell_score = 101 - buy_score

    g = {**DEFAULT_GATE, **(gate or {})}

    # Direction first, then every gate is tested against it. Each failure is
    # recorded rather than just suppressed, so the UI can say *why* a bar that
    # looks tradable is being held back.
    # a perfectly balanced reading has no side; the old `net > 0 else "sell"`
    # reported that a dead-neutral bar "wanted" to sell
    wanted = "buy" if net > 0 else "sell" if net < 0 else "neutral"
    blocks: list[dict] = []
    if abs(net) < g["min_net"]:
        # three decimals: at two, a net of 0.1187 rendered as "0.12 need ≥ 0.12",
        # which reads like a gate that should have passed
        blocks.append({"gate": "directional lean", "need": f"|net| ≥ {g['min_net']:.3f}",
                       "got": f"{abs(net):.3f}"})
    if score < g["min_score"]:
        blocks.append({"gate": "score", "need": f"≥ {g['min_score']}", "got": str(score)})
    if agree < g["min_agreement"]:
        blocks.append({"gate": "agreement", "need": f"≥ {g['min_agreement']:.0%}",
                       "got": f"{agree:.0%}"})
    if g["block_squeeze"] and vol["regime"] == "squeeze":
        blocks.append({"gate": "volatility regime", "need": "not a squeeze",
                       "got": "squeeze — direction undecided"})
    if g["min_timing"] and timing_score is not None and timing_score < g["min_timing"]:
        blocks.append({"gate": "session quality", "need": f"≥ {g['min_timing']}",
                       "got": str(timing_score)})

    action = "wait" if (blocks or wanted == "neutral") else wanted
    conviction = ("high" if score >= 65 else "moderate" if score >= 45
                  else "low" if score >= 30 else "very low")
    plan = _plan(net, action, zone_payload, snap)

    # R:R can only be judged once a plan exists, so it gates after the fact and
    # sends the action back to wait if the location is not worth the risk.
    if action != "wait" and g["min_rr"] and (plan.get("rr") or 0) < g["min_rr"]:
        blocks.append({"gate": "reward:risk", "need": f"≥ {g['min_rr']:.1f}:1",
                       "got": f"{plan.get('rr')}:1"})
        action = "wait"
        plan = _plan(net, "wait", zone_payload, snap)
    lev = _leverage(plan, snap, qty, leverages)

    # ---- narrative, built from whatever actually contributed ----
    ranked = sorted(comps.items(),
                    key=lambda kv: -abs(kv[1]["value"] * WEIGHTS[kv[0]]))
    supporting = [(k, c) for k, c in ranked
                  if abs(c["value"]) > 0.1 and (c["value"] > 0) == (net > 0)]
    against = [(k, c) for k, c in ranked
               if abs(c["value"]) > 0.1 and (c["value"] > 0) != (net > 0)]

    if action == "wait" and blocks:
        why = "; ".join(f"{b['gate']} {b['got']} (need {b['need']})" for b in blocks)
        headline = (f"Wait — {symbol} {tf} leans {wanted} (buy {buy_score} / "
                    f"sell {sell_score}, score {score}) but fails your gate: {why}.")
    elif action == "wait":
        headline = (f"Wait. {symbol} on {tf} scores {score}/100 with no clear side "
                    f"(buy {buy_score} / sell {sell_score}).")
    else:
        headline = (f"{action.upper()} bias on {symbol} {tf} — {score}/100, "
                    f"{conviction} conviction (buy {buy_score} / sell {sell_score}).")

    reasoning = [f"{k.replace('_', ' ')} — {c['detail']}" for k, c in supporting[:5]]
    risks = [f"{k.replace('_', ' ')} — {c['detail']}" for k, c in against[:4]]
    risks.append(f"volatility — {vol['detail']}")
    if action != "wait" and plan.get("rr") is not None and plan["rr"] < 1.2:
        risks.append(f"reward-to-risk is only {plan['rr']}:1 to the next zone — "
                     f"the location is poor even if the direction is right")

    execution = []
    if action == "wait" and blocks:
        execution.append(
            f"Not a trade under your current gate. The bar leans {wanted}, but "
            + "; ".join(f"{b['gate']} is {b['got']} and needs {b['need']}"
                        for b in blocks) + ".")
    if action == "wait":
        w = plan.get("watch") or {}
        if w.get("long_above"):
            execution.append(f"Long only becomes interesting on an accepted close above "
                             f"{w['long_above']:,.2f}.")
        if w.get("short_below"):
            execution.append(f"Short only becomes interesting on an accepted close below "
                             f"{w['short_below']:,.2f}.")
        execution.append("Most bars are 'wait'. Not trading this bar costs nothing; "
                         "trading a 30/100 bar at 300x costs the account.")
    else:
        execution.append(f"Entry {plan['entry']:,.2f}, stop {plan['stop']:,.2f} "
                         f"({plan['risk_pct']:.2f}%), first target "
                         f"{plan['targets'][0]['price']:,.2f} "
                         f"({plan['targets'][0]['r']}R).")
        execution.append(f"Invalidated by {plan['invalidation']}")
        if (plan.get("structural_r") or 0) > 2:
            execution.append(
                f"The next actual level is {plan['structural_level']:,.2f}, "
                f"{plan['structural_r']}R away — too far for one scalp on {tf}, so T1 "
                f"is arithmetic (2R) rather than structure. Take partials there.")
        if lev.get("max_leverage_for_this_stop"):
            execution.append(
                f"At {qty} {symbol.replace('USDT', '')} that stop is "
                f"{plan['risk_pct']:.2f}% of price, so keep leverage at or below "
                f"{lev['max_leverage_for_this_stop']}x — above that the liquidation "
                f"price is inside your stop.")

    return {
        "symbol": symbol,
        "tf": tf,
        "score": score,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "action": action,
        "wanted_action": wanted,
        "gate": g,
        "gate_blocks": blocks,
        "gate_passed": not blocks,
        "conviction": conviction,
        "net": round(net, 4),
        "agreement": round(agree, 3),
        "quality": round(quality, 3),
        "regime": vol["regime"],
        "volatility": vol,
        "components": [
            {"key": k, "weight": WEIGHTS[k], "value": round(c["value"], 3),
             "contribution": round(WEIGHTS[k] * c["value"] / den, 4),
             "direction": ("bullish" if c["value"] > 0.08 else
                           "bearish" if c["value"] < -0.08 else "neutral"),
             "detail": c["detail"], "extra": c.get("extra")}
            for k, c in ranked
        ],
        "advice": {
            "headline": headline,
            "supporting": reasoning,
            "risks": risks,
            "execution": execution,
        },
        "plan": plan,
        "leverage": lev,
        "zones": zone_payload,
        "snapshot": snap,
        "disclaimer": ("Measures agreement between live conditions on closed candles. "
                       "Not a probability, not an expected return, not advice."),
    }
