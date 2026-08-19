"""Confluence scoring: 0-100 per direction, plus a plain-language explanation.

The score measures how many independent conditions line up — it is a measure
of agreement, not of expected profit.
"""

from __future__ import annotations

SETUP_WEIGHTS = {
    "orb": 12, "vwap_fade": 10, "ema_pullback": 12, "rsi2_extreme": 8,
    "funding_extreme": 14, "liquidation_cascade": 14, "volume_spike": 10,
    "session_open_volatility": 6, "structure_break": 12, "fvg": 8,
    "order_block": 8, "sr_reaction": 8, "trend_continuation": 12,
    "mean_reversion": 8,
    # retail scalping playbook
    "ema_cross_pullback": 13, "vwap_bounce": 11, "liquidity_grab": 15,
    "rsi_sr_confluence": 9, "breakout_retest": 14, "bb_squeeze": 10,
}


def score(events: list[dict], snap: dict) -> dict:
    longs = [e for e in events if e["direction"] == "long"]
    shorts = [e for e in events if e["direction"] == "short"]
    side_events, direction = ((longs, "long") if len(longs) >= len(shorts)
                              else (shorts, "short"))
    if not side_events:
        return {"score": 0, "direction": "neutral", "reasons": []}

    total = 0.0
    reasons = []
    for e in side_events:
        w = SETUP_WEIGHTS.get(e["setup"], 8) * e.get("strength", 0.5) / 0.7
        total += w
        reasons.append(e["detail"])

    c, e200 = snap.get("close"), snap.get("ema200")
    if c and e200:
        trend_up = c > e200
        if (direction == "long") == trend_up:
            total += 10
            reasons.append(f"aligned with EMA200 trend (price {'above' if trend_up else 'below'})")
        else:
            total -= 5
            reasons.append("against the EMA200 trend")

    fr = snap.get("funding_rate")
    if fr is not None:
        if (direction == "long" and fr < 0) or (direction == "short" and fr > 0):
            total += 8
            reasons.append(f"funding ({fr:+.4%}) leans in favor")

    vz = snap.get("vol_z")
    if vz is not None and vz > 1:
        total += 8
        reasons.append(f"volume confirmation (z={vz:.1f})")

    ap = snap.get("atr_pct")
    if ap is not None and ap > 0.02:
        total -= 8
        reasons.append("volatility unusually high — wider risk")

    opposite = shorts if direction == "long" else longs
    if opposite:
        total -= 6 * len(opposite)
        reasons.append(f"{len(opposite)} conflicting signal(s) the other way")

    return {"score": int(max(0, min(100, round(total)))),
            "direction": direction, "reasons": reasons}


def tier(s: int) -> str:
    return "high" if s >= 60 else "medium" if s >= 35 else "low"


def explain(symbol: str, tf: str, events: list[dict], conf: dict,
            snap: dict) -> str:
    if not events:
        return ""
    parts = [e["detail"] for e in events]
    fr = snap.get("funding_rate")
    ctx_bits = []
    if snap.get("close") and snap.get("ema200"):
        ctx_bits.append("price is "
                        + ("above" if snap["close"] > snap["ema200"] else "below")
                        + " EMA200")
    if fr is not None:
        ctx_bits.append(f"funding rate {fr:+.4%}")
    return (f"{symbol} {tf}: " + "; ".join(parts)
            + (". Context: " + ", ".join(ctx_bits) if ctx_bits else "")
            + f". Confluence score: {conf['score']}/100 ({tier(conf['score'])},"
              f" {conf['direction']}-leaning). Setup occurrence only — not advice.")
