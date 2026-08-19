"""Vectorized indicators used by the strategies. All take/return pandas objects."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100.0).where(avg_loss != 0, 100.0).where(avg_gain != 0, 0.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range on columns high/low/close."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def session_id(index: pd.DatetimeIndex) -> pd.Series:
    """UTC calendar date used to group bars into daily sessions."""
    return pd.Series(index.date, index=index)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP reset at each UTC session start, using typical price."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = tp * df["volume"]
    sid = session_id(df.index)
    cum_pv = pv.groupby(sid.values).cumsum()
    cum_v = df["volume"].groupby(sid.values).cumsum()
    return cum_pv / cum_v.replace(0.0, np.nan)


def session_vwap_bands(df: pd.DataFrame, n_std: float = 2.0) -> pd.DataFrame:
    """VWAP plus/minus n_std rolling deviations of price around VWAP.

    Deviation is the cumulative session std of (typical price - vwap).
    """
    vwap = session_vwap(df)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    dev = tp - vwap
    sid = session_id(df.index)
    # cumulative std within session (expanding); min 12 bars to stabilize
    std = dev.groupby(sid.values).expanding().std().reset_index(level=0, drop=True)
    std.index = df.index
    count = dev.groupby(sid.values).cumcount() + 1
    std = std.where(count.values >= 12)
    return pd.DataFrame({
        "vwap": vwap,
        "upper": vwap + n_std * std,
        "lower": vwap - n_std * std,
    }, index=df.index)


def opening_range(df: pd.DataFrame, minutes: int = 30,
                  session_start_hour: int = 0) -> pd.DataFrame:
    """High/low of the first `minutes` of each UTC session.

    Returns per-bar columns: or_high, or_low, in_range_window (bool for bars
    inside the opening window, where no entries are allowed).
    """
    ts = df.index
    minute_of_day = ts.hour * 60 + ts.minute
    start_min = session_start_hour * 60
    offset = (minute_of_day - start_min) % (24 * 60)
    in_window = offset < minutes

    sid = pd.Series(((ts - pd.Timedelta(hours=session_start_hour)).date), index=ts)

    win_high = df["high"].where(in_window)
    win_low = df["low"].where(in_window)
    or_high = win_high.groupby(sid.values).cummax().ffill()
    or_low = win_low.groupby(sid.values).cummin().ffill()
    # only valid after window closes
    or_high = or_high.where(~in_window)
    or_low = or_low.where(~in_window)

    return pd.DataFrame({
        "or_high": or_high,
        "or_low": or_low,
        "in_range_window": in_window,
    }, index=df.index)
