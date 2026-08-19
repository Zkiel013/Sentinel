"""Live market data: Binance WebSocket klines + funding poll + liquidation stream.

Free public endpoints, no API key. Keeps a rolling in-memory candle store per
(symbol, timeframe); fires a callback on every closed candle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque

import pandas as pd
import requests
import websockets

log = logging.getLogger("sentinel.data")

SPOT_WS = "wss://stream.binance.com:9443/stream"
FUT_WS = "wss://fstream.binance.com/stream"
SPOT_KLINES_URL = "https://api.binance.com/api/v3/klines"
FUT_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"
FUNDING_HIST_URL = "https://fapi.binance.com/fapi/v1/fundingRate"

# Which venue the candles come from.
#
# This used to be spot unconditionally, while funding and liquidations were read
# from futures — three inputs describing two different order books. The perpetual
# trades at a basis to spot (around -0.05% when measured, which is roughly 0.4x a
# 5m range and about a quarter of a typical scalp stop), so zone edges, entries
# and stops were derived from a price series that is not the one being traded.
#
# Futures pricing is the more correct input — everything else here is already
# perp-derived, and leveraged crypto CFDs track perpetual pricing far more
# closely than spot. But fstream.binance.com is unreachable from a number of
# regions (India among them): the REST endpoint answers, so warmup fills in and
# the socket reports "connected", yet no kline ever arrives and the chart
# silently freezes on warmup data. Spot is therefore the default, with the
# venue selectable and an automatic fallback below.
DEFAULT_MARKET = "spot"

MAX_BARS = 600

# A kline stream that goes quiet for this long is treated as dead. Even the
# slowest subscribed timeframe sends forming-candle updates every 1-2 seconds,
# so silence never means "nothing happened".
STALL_SEC = 45



class MarketData:
    def __init__(self, symbols: list[str], timeframes: list[str],
                 market: str = DEFAULT_MARKET, allow_fallback: bool = True):
        self.symbols = [s.upper() for s in symbols]
        self.timeframes = timeframes
        self.allow_fallback = allow_fallback
        self.fell_back_to = None
        self.last_msg_at = None
        self._use_market(market)
        self.candles: dict[tuple[str, str], deque] = {}
        self.funding: dict[str, dict] = {}          # sym -> {rate, history deque}
        self.liquidations: dict[str, deque] = {}    # sym -> deque[(ts_ms, side, notional)]
        self.on_bar_close = None                    # async callback(sym, tf)
        self.on_tick = None                         # async callback(sym, tf, bar) for forming candles
        self.connected = False

    # ---------- access ----------

    def df(self, sym: str, tf: str) -> pd.DataFrame:
        rows = list(self.candles.get((sym, tf), []))
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close",
                                         "volume", "taker_buy_base"])
        df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
        df.index.name = "open_time"
        return df.drop(columns=["t"])

    def funding_rate(self, sym: str) -> float | None:
        return self.funding.get(sym, {}).get("rate")

    def funding_history(self, sym: str) -> list[float]:
        return list(self.funding.get(sym, {}).get("history", []))

    def recent_liquidations(self, sym: str, window_sec: int = 60) -> list:
        cutoff = time.time() * 1000 - window_sec * 1000
        return [x for x in self.liquidations.get(sym, []) if x[0] >= cutoff]

    # ---------- warmup ----------

    def warmup(self):
        for sym in self.symbols:
            for tf in self.timeframes:
                try:
                    raw = requests.get(self.klines_url, params={
                        "symbol": sym, "interval": tf, "limit": MAX_BARS,
                    }, timeout=30).json()
                    dq = deque(maxlen=MAX_BARS)
                    for k in raw[:-1]:  # last kline is still open
                        dq.append((k[0], float(k[1]), float(k[2]), float(k[3]),
                                   float(k[4]), float(k[5]), float(k[9])))
                    self.candles[(sym, tf)] = dq
                    log.info("warmup %s %s: %d bars", sym, tf, len(dq))
                except Exception as e:
                    log.warning("warmup failed %s %s: %s", sym, tf, e)
                    self.candles.setdefault((sym, tf), deque(maxlen=MAX_BARS))
                time.sleep(0.1)
            try:
                hist = requests.get(FUNDING_HIST_URL, params={
                    "symbol": sym, "limit": 270,  # ~90 days
                }, timeout=30).json()
                rates = deque((float(x["fundingRate"]) for x in hist), maxlen=1000)
                self.funding[sym] = {"rate": rates[-1] if rates else None,
                                     "history": rates}
            except Exception as e:
                log.warning("funding warmup failed %s: %s", sym, e)
                self.funding[sym] = {"rate": None, "history": deque(maxlen=1000)}
            self.liquidations.setdefault(sym, deque(maxlen=2000))

    # ---------- live tasks ----------

    async def run(self):
        await asyncio.gather(
            self._kline_loop(),
            self._funding_loop(),
            self._liquidation_loop(),
        )

    def _use_market(self, market: str):
        self.market = "spot" if market == "spot" else "futures"
        self.klines_url = (SPOT_KLINES_URL if self.market == "spot"
                           else FUT_KLINES_URL)
        self.kline_ws = SPOT_WS if self.market == "spot" else FUT_WS

    async def _kline_loop(self):
        streams = "/".join(f"{s.lower()}@kline_{tf}"
                           for s in self.symbols for tf in self.timeframes)
        dead_connects = 0
        while True:
            url = f"{self.kline_ws}?streams={streams}"
            received = 0
            try:
                async with websockets.connect(url, ping_interval=20,
                                              open_timeout=15) as ws:
                    self.connected = True
                    log.info("kline stream connected: %s market, %d streams",
                             self.market,
                             len(self.symbols) * len(self.timeframes))
                    while True:
                        # recv() with a deadline instead of `async for`: a socket
                        # that opens and then never delivers looked identical to a
                        # healthy idle one, so the chart froze on warmup data with
                        # "connected" still showing green.
                        msg = await asyncio.wait_for(ws.recv(), timeout=STALL_SEC)
                        received += 1
                        self.last_msg_at = time.time()
                        k = json.loads(msg).get("data", {}).get("k")
                        if not k:
                            continue
                        sym, tf = k["s"], k["i"]
                        if not k.get("x"):            # candle still forming
                            if self.on_tick:
                                try:
                                    await self.on_tick(sym, tf, {
                                        "time": k["t"] // 1000,
                                        "open": float(k["o"]),
                                        "high": float(k["h"]),
                                        "low": float(k["l"]),
                                        "close": float(k["c"]),
                                    })
                                except Exception:
                                    log.exception("tick handler failed")
                            continue
                        dq = self.candles.setdefault((sym, tf),
                                                     deque(maxlen=MAX_BARS))
                        dq.append((k["t"], float(k["o"]), float(k["h"]),
                                   float(k["l"]), float(k["c"]),
                                   float(k["v"]), float(k["V"])))
                        if self.on_bar_close:
                            try:
                                await self.on_bar_close(sym, tf)
                            except Exception:
                                log.exception("bar-close handler failed")
            except Exception as e:
                self.connected = False
                stalled = isinstance(e, asyncio.TimeoutError)
                log.warning("kline stream %s on %s (%s); %d messages this session",
                            "stalled" if stalled else "dropped", self.market,
                            type(e).__name__, received)

                # A socket that connected but never delivered a single message is
                # a blocked venue, not a blip. Two of those in a row and we move
                # to the other venue and re-warm from it, so the price series
                # stays internally consistent.
                if received == 0:
                    dead_connects += 1
                else:
                    dead_connects = 0
                if dead_connects >= 2 and self.allow_fallback:
                    other = "spot" if self.market == "futures" else "futures"
                    log.warning("no data from the %s kline stream — falling back "
                                "to %s", self.market, other)
                    self._use_market(other)
                    self.fell_back_to = other
                    dead_connects = 0
                    try:
                        await asyncio.to_thread(self.warmup)
                    except Exception:
                        log.exception("re-warmup after venue fallback failed")
                await asyncio.sleep(5)

    async def _funding_loop(self):
        while True:
            for sym in self.symbols:
                try:
                    r = await asyncio.to_thread(
                        requests.get, PREMIUM_URL,
                        params={"symbol": sym}, timeout=15)
                    rate = float(r.json().get("lastFundingRate", 0))
                    slot = self.funding.setdefault(
                        sym, {"rate": None, "history": deque(maxlen=1000)})
                    if slot["rate"] != rate:
                        slot["history"].append(rate)
                    slot["rate"] = rate
                except Exception as e:
                    log.debug("funding poll failed %s: %s", sym, e)
            await asyncio.sleep(60)

    async def _liquidation_loop(self):
        streams = "/".join(f"{s.lower()}@forceOrder" for s in self.symbols)
        url = f"{FUT_WS}?streams={streams}"
        while True:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    log.info("liquidation stream connected")
                    async for msg in ws:
                        o = json.loads(msg).get("data", {}).get("o")
                        if not o:
                            continue
                        sym = o["s"]
                        notional = float(o["ap"]) * float(o["q"])
                        self.liquidations.setdefault(
                            sym, deque(maxlen=2000)).append(
                            (o["T"], o["S"], notional))
            except Exception as e:
                log.warning("liquidation stream dropped (%s), retry in 10s", e)
                await asyncio.sleep(10)
