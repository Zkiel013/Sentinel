"""Dynamic multi-timeframe support / resistance zones.

A zone is a price *band* (not a line) built by clustering confirmed swing
pivots that sit within a fraction of ATR of each other. Zones carry a
strength score, a visit/rejection history, and a broken flag.

Everything is recomputed from live candles on every closed bar, so zones
drift, widen, merge and die on their own — nothing is hard-coded.

Higher-timeframe invalidation
-----------------------------
Zones are built per timeframe, then merged. When a *higher* timeframe zone
is broken (a decisive close beyond it), every lower-timeframe zone whose band
falls inside that broken band is demoted or dropped: the structure that
mattered on 1m stops mattering once the 1h level it lived inside gives way.
That is what makes the drawn rectangles self-adjust instead of piling up.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

try:                       # normal case: imported as part of the package
    from . import indicators as ind
except ImportError:        # fallback: module run directly, not as a package
    import indicators as ind

TF_ORDER = ["1m", "5m", "15m", "1h", "4h", "1d"]

# How much a zone from each timeframe is worth. Higher timeframe = more
# participants defended it, so it survives longer and is weighted up.
TF_WEIGHT = {"1m": 0.30, "5m": 0.45, "15m": 0.62, "1h": 0.82, "4h": 1.00,
             "1d": 1.15}

# Fractal half-width used to confirm a pivot per timeframe. Lower timeframes
# are noisier, so they need more bars of confirmation on each side.
PIVOT_HALF = {"1m": 6, "5m": 5, "15m": 4, "1h": 4, "4h": 3, "1d": 3}

CLUSTER_ATR = 0.45     # pivots within this * ATR merge into one zone
MIN_WIDTH_ATR = 0.18   # a zone is never thinner than this * ATR
MAX_WIDTH_ATR = 1.60   # cap so a cluster cannot smear into a whole range
BREAK_ATR = 0.55       # close must clear the band by this * ATR to break it
DEAD_DISTANCE_ATR = 14  # broken zone this far behind price is discarded


# How far away a zone can be and still be worth drawing. Measured as the
# looser of the two bounds on purpose: 20x ATR is the right scale on 15m and
# above, but on 1m it is a fraction of a percent, which would throw away the
# higher-timeframe levels sitting overhead — exactly the ones a scalper needs
# to see. The percentage floor keeps them.
MAX_DISTANCE_ATR = 20
MAX_DISTANCE_PCT = 0.035


def tf_rank(tf: str) -> int:
    return TF_ORDER.index(tf) if tf in TF_ORDER else 0


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ---------------------------------------------------------------- pivots

def _pivot_points(df: pd.DataFrame, half: int) -> tuple[list, list]:
    """Confirmed fractal swing highs / lows.

    Returns two lists of (bar_index, price, volume). Confirmation lags by
    `half` bars — a pivot only exists once `half` bars have printed after it,
    which is what stops the zones from repainting.
    """
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)
    n = len(h)
    highs, lows = [], []
    for i in range(half, n - half):
        win_h = h[i - half:i + half + 1]
        win_l = l[i - half:i + half + 1]
        if h[i] >= win_h.max():
            highs.append((i, float(h[i]), float(v[i])))
        if l[i] <= win_l.min():
            lows.append((i, float(l[i]), float(v[i])))
    return highs, lows


def _cluster(points: list, tol: float) -> list[list]:
    """Greedy price clustering: sort by price, cut whenever the gap > tol."""
    if not points:
        return []
    pts = sorted(points, key=lambda p: p[1])
    groups, cur = [], [pts[0]]
    for p in pts[1:]:
        if p[1] - cur[-1][1] <= tol:
            cur.append(p)
        else:
            groups.append(cur)
            cur = [p]
    groups.append(cur)
    return groups


# ---------------------------------------------------------------- per-tf zones

def _zone_stats(df: pd.DataFrame, lo: float, hi: float, atr: float,
                born: int, origin: str) -> dict:
    """Visits, rejections and break state for a band, measured after birth.

    "Broken" is judged by role, not by any close that ever poked through: a
    band built from swing *lows* is broken only when price now sits decisively
    *below* it (it failed as support), and one built from swing *highs* only
    when price sits above it. Counting every historical excursion would mark
    almost every level broken over a 600-bar window, which is why the test is
    on the current side rather than on history.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    n = len(close)

    inside = (low <= hi) & (high >= lo)
    # Which side counts as "held" for this band: a support holds when price
    # leaves upward, a resistance holds when price leaves downward.
    hold_up = origin == "support"

    visits = 0
    rejections = 0     # visit ended back on the origin side — the level held
    penetrations = 0   # visit ended on the far side — the level gave way
    visit_vol = []
    last_touch = None
    i = born
    while i < n:
        if not inside[i]:
            i += 1
            continue
        visits += 1
        visit_vol.append(vol[i])
        # walk to the end of this visit and record which side it exited on
        j = i
        while j < n and inside[j]:
            last_touch = j
            j += 1
        if j < n:
            exited_up = close[j] > hi
            if exited_up == hold_up:
                rejections += 1
            else:
                penetrations += 1
        i = j + 1

    # how many times it was poked through and reclaimed — that is defence,
    # and it makes the level stronger rather than broken
    defended = 0
    for i in range(born + 1, n):
        if origin == "support" and close[i - 1] < lo and close[i] > lo:
            defended += 1
        elif origin == "resistance" and close[i - 1] > hi and close[i] < hi:
            defended += 1

    last = close[-1]
    broken = False
    broken_dir = None
    if origin == "support" and last < lo - BREAK_ATR * atr:
        broken, broken_dir = True, "down"
    elif origin == "resistance" and last > hi + BREAK_ATR * atr:
        broken, broken_dir = True, "up"

    broken_at = None
    if broken:
        # first bar of the excursion price never came back from
        for i in range(n - 1, born - 1, -1):
            inside_or_back = (close[i] > lo if broken_dir == "down"
                              else close[i] < hi)
            if inside_or_back:
                broken_at = min(i + 1, n - 1)
                break

    return {
        "visits": visits,
        "rejections": rejections,
        "penetrations": penetrations,
        "defended": defended,
        "visit_volume": float(np.mean(visit_vol)) if visit_vol else 0.0,
        "last_touch": last_touch,
        "broken": broken,
        "broken_at": broken_at,
        "broken_dir": broken_dir,
    }


def zones_for_tf(df: pd.DataFrame, tf: str, max_zones: int = 14) -> list[dict]:
    """Build the zone list for one timeframe from its own candles."""
    if df is None or len(df) < 60:
        return []
    atr_s = ind.atr(df, 14)
    atr = float(atr_s.iloc[-1])
    if not math.isfinite(atr) or atr <= 0:
        return []
    price = float(df["close"].iloc[-1])
    half = PIVOT_HALF.get(tf, 5)
    highs, lows = _pivot_points(df, half)
    vol_mean = float(df["volume"].tail(120).mean()) or 1.0
    n = len(df)
    ts = df.index

    out = []
    for kind, points in (("resistance", highs), ("support", lows)):
        for grp in _cluster(points, CLUSTER_ATR * atr):
            prices = [p[1] for p in grp]
            lo, hi = min(prices), max(prices)
            width = hi - lo
            if width < MIN_WIDTH_ATR * atr:
                pad = (MIN_WIDTH_ATR * atr - width) / 2
                lo, hi = lo - pad, hi + pad
            elif width > MAX_WIDTH_ATR * atr:
                mid = (lo + hi) / 2
                lo = mid - MAX_WIDTH_ATR * atr / 2
                hi = mid + MAX_WIDTH_ATR * atr / 2
            born = min(p[0] for p in grp)
            st = _zone_stats(df, lo, hi, atr, born, kind)

            # discard broken zones price has long left behind
            if st["broken"] and abs(price - (lo + hi) / 2) > DEAD_DISTANCE_ATR * atr:
                continue

            age = n - 1 - born
            recency_bars = n - 1 - (st["last_touch"] if st["last_touch"] is not None else born)
            # exponential recency decay: a level untouched for 200 bars is stale
            recency = math.exp(-recency_bars / 160)
            vol_factor = min(1.0, st["visit_volume"] / vol_mean) if vol_mean else 0.0

            # Visits are normalised per 100 bars of the zone's own life so a
            # 4h zone and a 1m zone are scored on the same footing instead of
            # the older one simply accumulating more touches.
            life = max(age, 20)
            visit_rate = st["visits"] / life * 100
            decided = st["rejections"] + st["penetrations"]
            # hold rate: of the visits that resolved, how many left the way a
            # working level should. 0.5 means the band is coin-flip noise.
            hold_rate = st["rejections"] / decided if decided else 0.5

            strength = (0.22 * min(visit_rate, 4.0) / 4.0
                        + 0.26 * _clip01((hold_rate - 0.35) / 0.5)
                        + 0.10 * min(st["defended"], 4) / 4
                        + 0.12 * vol_factor
                        + 0.30 * (TF_WEIGHT.get(tf, 0.5) / TF_WEIGHT["1d"]))
            strength *= 0.45 + 0.55 * recency
            if st["broken"]:
                strength *= 0.40          # flipped level, still reactive
            strength = float(max(0.0, min(1.0, strength)))

            role = kind
            if st["broken"]:
                role = "flip"
            elif lo <= price <= hi:
                role = "inside"
            elif hi < price:
                role = "support"
            elif lo > price:
                role = "resistance"

            out.append({
                "tf": tf,
                "lo": round(lo, 2),
                "hi": round(hi, 2),
                "mid": round((lo + hi) / 2, 2),
                "origin": kind,
                "role": role,
                "pivots": len(grp),
                "visits": st["visits"],
                "rejections": st["rejections"],
                "penetrations": st["penetrations"],
                "hold_rate": round(hold_rate, 2),
                "defended": st["defended"],
                "broken": st["broken"],
                "broken_dir": st["broken_dir"],
                "strength": round(strength, 3),
                "age_bars": int(age),
                "bars_since_touch": int(recency_bars),
                "born_ts": ts[born].isoformat(),
                "last_touch_ts": (ts[st["last_touch"]].isoformat()
                                  if st["last_touch"] is not None else None),
                "atr": round(atr, 2),
                "tfs": [tf],
            })

    out.sort(key=lambda z: -z["strength"])
    return out[:max_zones]


# ---------------------------------------------------------------- MTF merge

def _overlap(a: dict, b: dict) -> float:
    """Fraction of the narrower band that the two bands share."""
    lo = max(a["lo"], b["lo"])
    hi = min(a["hi"], b["hi"])
    if hi <= lo:
        return 0.0
    span = min(a["hi"] - a["lo"], b["hi"] - b["lo"])
    return (hi - lo) / span if span > 0 else 0.0


def merge_mtf(per_tf: dict[str, list[dict]], price: float,
              atr: float, max_zones: int = 12) -> list[dict]:
    """Fold per-timeframe zones into one self-adjusting set.

    Order matters: highest timeframe first, so a 4h zone owns the band and
    lower-timeframe zones fold into it rather than the other way round.
    """
    ordered: list[dict] = []
    for tf in sorted(per_tf, key=tf_rank, reverse=True):
        ordered.extend(per_tf[tf])

    # A merged band must stay a band. Past this width it is a range, and
    # widening further would produce a rectangle too big to place a stop against.
    width_cap = 2.6 * atr

    kept: list[dict] = []
    for z in ordered:
        z.setdefault("counts_from", z["tf"])
        host = None
        for k in kept:
            if _overlap(k, z) <= 0.34:
                continue
            merged_w = max(k["hi"], z["hi"]) - min(k["lo"], z["lo"])
            # allow a band to grow, but not without limit: either it stays
            # inside the absolute cap, or it grows by at most 40% of what the
            # host already was (so wide higher-timeframe bands can still
            # absorb their neighbours proportionally).
            if merged_w > max(width_cap, 1.4 * (k["hi"] - k["lo"])):
                continue
            host = k
            break
        if host is None:
            kept.append(dict(z))
            continue
        host["lo"] = min(host["lo"], z["lo"])
        host["hi"] = max(host["hi"], z["hi"])
        host["mid"] = round((host["lo"] + host["hi"]) / 2, 2)
        host["pivots"] = max(host["pivots"], z["pivots"])

        # Touch counts move as one coherent set, from whichever contributing
        # timeframe observed the band most closely. Taking max() per field
        # independently (the first attempt) mixed timeframes inside one row and
        # produced impossible readings — 26 visits with 21 held and 13 gave way.
        # The same touch seen on 5m and 1h is one touch, so they are never summed.
        if z["visits"] > host["visits"]:
            for k in ("visits", "rejections", "penetrations", "defended"):
                host[k] = z[k]
            host["counts_from"] = z["tf"]
        if z["tf"] not in host["tfs"]:
            host["tfs"].append(z["tf"])
        host["bars_since_touch"] = min(host["bars_since_touch"],
                                       z["bars_since_touch"])

    for z in kept:
        # hold_rate has to be recomputed from the merged counts, not carried
        # over from whichever contributor happened to be the host
        decided = z["rejections"] + z["penetrations"]
        z["hold_rate"] = round(z["rejections"] / decided, 2) if decided else 0.5
        # multi-timeframe agreement is real evidence, with diminishing returns
        z["strength"] = round(min(1.0, z["strength"] * (1 + 0.07 * (len(z["tfs"]) - 1))), 3)

    # ---- higher-timeframe break invalidation -------------------------------
    # When a higher-timeframe level fails, price has traded *through* it. Any
    # lower-timeframe structure sitting on the far side of that failure is
    # stale: it was built inside a regime that no longer exists. Demote it so
    # the drawn rectangles thin out on their own instead of accumulating.
    for big in [z for z in kept if z["broken"]]:
        for small in kept:
            if small is big or small["broken"]:
                continue
            if tf_rank(small["tf"]) >= tf_rank(big["tf"]):
                continue
            nested = small["lo"] >= big["lo"] - atr and small["hi"] <= big["hi"] + atr
            # behind the break: above a failed support, or below a failed resistance
            behind = ((big["broken_dir"] == "down" and small["mid"] > big["lo"])
                      or (big["broken_dir"] == "up" and small["mid"] < big["hi"]))
            if nested or behind:
                small["invalidated_by"] = f"{big['tf']} break at {big['mid']:,.0f}"
                small["strength"] = round(small["strength"] * 0.35, 3)

    for z in kept:
        z.setdefault("invalidated_by", None)
        dist = z["mid"] - price
        z["distance"] = round(dist, 2)
        z["distance_pct"] = round(dist / price * 100, 3) if price else 0.0
        z["distance_atr"] = round(dist / atr, 2) if atr else 0.0
        z["width"] = round(z["hi"] - z["lo"], 2)
        z["width_atr"] = round(z["width"] / atr, 2) if atr else 0.0
        z["inside"] = z["lo"] <= price <= z["hi"]
        if z["inside"]:
            z["role"] = "inside"
        elif z["broken"]:
            z["role"] = "flip"
        else:
            z["role"] = "support" if z["hi"] < price else "resistance"
        z["tfs"] = sorted(z["tfs"], key=tf_rank, reverse=True)
        z["tier"] = ("major" if z["strength"] >= 0.70
                     else "medium" if z["strength"] >= 0.45 else "minor")
        # what to rank by: a strong level 20 ATR away is not what a scalper
        # needs drawn, so relevance discounts strength by distance
        z["relevance"] = round(z["strength"] / (1 + abs(z["distance_atr"]) / 5), 4)
        z["label"] = f"{z['tier']} {z['role']} · {'/'.join(z['tfs'])}"

    # keep a balanced picture: the most relevant on each side plus whatever
    # price is currently sitting in, rather than ten supports and no resistance
    per_side = max(2, max_zones // 2)
    reach = max(MAX_DISTANCE_ATR * atr, MAX_DISTANCE_PCT * price)
    in_range = [z for z in kept if abs(z["distance"]) <= reach]
    top: list[dict] = []
    for role in ("support", "resistance"):
        pool = sorted([z for z in in_range if z["role"] == role],
                      key=lambda z: -z["relevance"])
        if not pool:
            # never leave a side of the book empty — the trade plan and the
            # "what to watch" levels both need something on each side
            far = [z for z in kept if z["role"] == role]
            if far:
                pool = [min(far, key=lambda z: abs(z["distance"]))]
        top.extend(pool[:per_side])
    top.extend(z for z in in_range if z["role"] in ("inside", "flip"))
    top.sort(key=lambda z: z["mid"])
    return top


def nearest(zones: list[dict], price: float, role: str,
            max_width_atr: float | None = None) -> dict | None:
    """Closest zone of a role, measured to its near edge.

    Targets should use the unfiltered result — a target only needs the near
    edge, so how wide the band is does not matter. Stops must pass
    `max_width_atr`, because a stop is anchored to the *far* edge and the far
    edge of a range-sized band sits several percent away, which is fatal at
    100-400x.
    """
    pool = [z for z in zones if z["role"] == role and not z["inside"]]
    if max_width_atr is not None:
        pool = [z for z in pool if z.get("width_atr", 0) <= max_width_atr]
    if not pool:
        return None
    return min(pool, key=lambda z: min(abs(z["lo"] - price), abs(z["hi"] - price)))


def build(frames: dict[str, pd.DataFrame], tf: str) -> dict:
    """Full zone payload for one chart timeframe.

    `frames` is every timeframe available for the symbol; zones are drawn on
    the `tf` chart but sourced from `tf` and everything above it.
    """
    df = frames.get(tf)
    if df is None or df.empty:
        return {"zones": [], "atr": None, "price": None}
    price = float(df["close"].iloc[-1])
    atr = float(ind.atr(df, 14).iloc[-1])
    use = [t for t in TF_ORDER if t in frames and tf_rank(t) >= tf_rank(tf)]
    per_tf = {t: zones_for_tf(frames[t], t) for t in use}
    zones = merge_mtf(per_tf, price, atr)
    return {
        "zones": zones,
        "atr": round(atr, 2),
        "price": round(price, 2),
        "sourced_from": use,
        # context and targets: nearest edge, any width
        "nearest_support": nearest(zones, price, "support"),
        "nearest_resistance": nearest(zones, price, "resistance"),
        # stop anchors: only bands tight enough to place a stop behind
        "stop_support": nearest(zones, price, "support", max_width_atr=2.6),
        "stop_resistance": nearest(zones, price, "resistance", max_width_atr=2.6),
        "inside": next((z for z in zones if z["inside"]), None),
        "flips": [z for z in zones if z["role"] == "flip"],
    }
