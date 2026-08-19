# Sentinel

Live crypto setup monitor. Watches Binance data, detects when technical setups
occur, scores how many independent conditions agree, maps dynamic support and
resistance, tells you whether the current hour is worth trading at all, and does
the position arithmetic for your own size and leverage.

It measures agreement between conditions. It is not a prediction, not a
probability, and not financial advice. See [The honest part](#the-honest-part).

---

## Running it

**Windows**

```powershell
git clone https://github.com/Zkiel013/Sentinel.git
cd Sentinel
.un.ps1
```

**macOS / Linux**

```bash
git clone https://github.com/Zkiel013/Sentinel.git
cd Sentinel
./run.sh
```

Then open <http://localhost:8777>. `Ctrl+C` to stop.
Use `-Port 8888` (or `PORT=8888 ./run.sh`) to run on another port.

First run creates `.venv` and installs from `requirements.txt` — a minute or two.
After that it starts immediately. Nothing else is needed: the database, preferences
and default rules are all created on first launch.

Needs Python 3.10+. No API key, no account, no frontend build step.

If PowerShell blocks the script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Keep the checkout path short

Clone somewhere like `C:\dev\sentinel`. Windows caps paths at 260 characters
unless long-path support is enabled, and `site-packages` paths inside a venv are
long — a deeply nested checkout fails while building the virtual environment,
with an error that points nowhere useful. `run.ps1` warns when the path looks
risky.

### Why a launcher rather than `python -m uvicorn`

Because bare `python` means different things in different shells, and that is
the entire cause of `No module named uvicorn` on a machine where uvicorn is
plainly installed. On this box `python` can resolve to:

- `C:\Python314\python.exe` — the real one
- `C:\Users\...\AppData\Local\Microsoft\WindowsApps\python.exe` — the Microsoft
  Store stub, which has no packages at all

and packages installed with `pip install --user` land in a *per-user*
`site-packages` that a shell opened before the install may not pick up. Same
command, same machine, different result depending on which terminal you typed it
into.

`.venv` removes the ambiguity: one interpreter, one package set, owned by the
project. `run.ps1` calls `.venv\Scripts\python.exe` by absolute path and never
consults `PATH`.

### Manual equivalent

Run from the **repository root**, not from inside `sentinel/` — `sentinel/` is a package, so uvicorn
needs its parent on the path.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn sentinel.server:app --port 8777
```

Add `--reload` while editing if you want restarts on save.

Needs Python 3.10+. No API key, no account, no frontend build step — the UI is a
single self-contained `static/index.html`.

### When an import fails anyway

Print the interpreter actually in use before assuming a package is missing:

```powershell
python -c "import sys; print(sys.executable)"
```

If it is not the `.venv` one, that is your answer.

### First run

Startup takes about 30 seconds:

1. Downloads 600 candles per symbol per timeframe (12 combinations by default).
2. Downloads 90 days of hourly history per symbol to build the session profiles.

Both run in the background; the UI works immediately and the Timing tab fills in
when its profile lands.

---

## Checking it is actually working

```bash
curl -s http://localhost:8777/api/diagnostics
```

The field that matters is **`seconds_since_last_message`**. Anything under ~5 is
healthy. If it climbs past 45 the stream has stalled and the server will
reconnect on its own.

`connected: true` alone is **not** proof of life — a socket can open and never
deliver anything, which is exactly what happens when a venue is blocked.

---

## Configuration

Everything lives in `sentinel.db` (SQLite, created automatically) and is edited
from the **Settings** tab. Nothing needs a config file.

| Setting | What it does |
|---|---|
| Theme | light / medium / dark |
| Trade gate | thresholds a bar must clear before it says buy or sell |
| Position sizing | quantity and leverage steps for the leverage table |
| Watchlist | symbols to monitor (**restart required** — streams are subscribed at startup) |
| Setups monitored | enable/disable individual detectors |
| Channels | Telegram, Discord, email, browser notifications, sounds |

### Market venue

`market` in prefs is `"spot"` or `"futures"`.

Futures pricing is the more correct input — funding and liquidation feeds are
already perp-derived, and leveraged CFDs track perpetual pricing far more
closely than spot, which runs at a basis of roughly 0.05% (about a quarter of a
typical scalp stop).

**But `fstream.binance.com` is blocked in several regions, India included.** The
REST endpoint still answers, so warmup succeeds and the socket reports connected
while no candle ever arrives. Spot is therefore the default. If the chosen venue
delivers nothing across two connection attempts, the server logs it, switches to
the other venue and re-warms — visible as `fell_back_to` in diagnostics.

---

## The tabs

**Analysis** — the 1-100 score. The big number is conviction in the recommended
action; the two meters are buy and sell scores, which always sum to 101 (the same
reading from either side). Nine weighted components, each expandable for what it
measures and how to read it. Below that: the trade plan, the leverage table, and
every zone currently drawn.

**Timing** — is right now worth trading. Scored from the symbol's own 90-day
hourly history on liquidity (trade count, not turnover), range versus cost,
directional efficiency, and thin-book integrity. Shows a 24-hour IST map, best
and worst windows, and adjusts live when an hour is running below its own normal
volume.

**Feed** — every closed candle where a detector fired.

**Alerts** — only your rules firing. These are what make sound.

**Rules** — no-code condition builder, nested AND/OR over setups, indicator
comparisons and score gates.

**Settings** — everything above.

---

## Chart

- Green/red bands are dynamic support and resistance, rebuilt every closed
  candle and merged across timeframes. Click one for its statistics.
- Fill opacity tracks strength. Solid border = major, dashed = medium/minor,
  dotted and faint = invalidated by a higher-timeframe break.
- Dashed lines are the current plan's entry, stop and targets.
- `⏱` in the header counts down to the candle close — signals only evaluate on a
  close.
- Zoom and scroll position are remembered across symbol and timeframe switches
  and across reloads. `⤢ fit` resets them.

---

## Layout

```
sentinel/
  server.py         FastAPI app, websocket broadcast, REST endpoints
  market_data.py    Binance streams, warmup, venue fallback
  detectors.py      20 setup detectors + the indicator snapshot
  zones.py          dynamic multi-timeframe support/resistance
  advice.py         scoring, trade plan, leverage maths, the gate
  tradability.py    session-quality scoring and the IST hour map
  confluence.py     per-bar confluence score and explanation
  rules.py          user rule engine
  descriptions.py   all UI copy, served over /api/docs
  store.py          SQLite persistence
  static/index.html the entire frontend
```

Depends on `../indicators.py` for EMA, RSI, ATR and VWAP.

---

## Useful endpoints

| Endpoint | Purpose |
|---|---|
| `/api/diagnostics` | health, bar counts, detector errors, feed age |
| `/api/analysis?symbol=&tf=` | full score, plan, zones (`&fresh=true` to bypass cache) |
| `/api/timing?symbol=` | session quality + 24-hour IST map |
| `/api/zones?symbol=&tf=` | just the bands |
| `/api/docs` | every description the UI renders |

---

## Troubleshooting

**Candles frozen, `connected: true`** — the venue's websocket is reachable but
silent. Check `seconds_since_last_message`; the fallback handles it within about
a minute. Confirm directly:

```bash
python -c "import asyncio,websockets;asyncio.run(websockets.connect('wss://fstream.binance.com/stream?streams=btcusdt@kline_1m',open_timeout=10).__aenter__())"
```

**Added a symbol, no data** — restart the server; streams subscribe at startup.
The API returns `restart_needed: true` when this applies.

**Timing tab says "still building"** — it needs 90 days of hourly history per
symbol. Refreshes itself when ready.

**No sound** — click the page once; browsers block audio before interaction.

**Analysis says wait constantly** — that is the gate doing its job. The strip
above the score names the failing threshold. Most bars are wait.

**Changed the UI and see nothing** — hard refresh, `Ctrl+Shift+R`.

**`No module named <anything>`** — you are not running the `.venv` interpreter.
Use `.\run.ps1`, or check with
`python -c "import sys; print(sys.executable)"`. See
[Why a launcher](#why-a-launcher-rather-than-python--m-uvicorn).

---

## The honest part

A companion backtester (kept in a separate project, not included here) tested
these setups on three years of BTCUSDT with
realistic fees and slippage: every canonical intraday strategy lost money after
costs, and an ML model on the same indicators found only a
statistically-real-but-economically-worthless edge.

The trade gate was set by replaying this exact scoring engine over ~1400 bars of
5m and 15m on BTC and ETH — 817 filled trades, each walked forward to stop or
target and charged an 0.08% round trip. Results:

| gate | trades | avg R |
|---|---|---|
| none | 817 | −0.404 |
| score ≥ 50 | 308 | −0.267 |
| score ≥ 50 + rr ≥ 1.8 + no squeeze *(default)* | 229 | −0.189 |

The default roughly halves the loss and removes about 72% of signals. **Every
threshold is still negative.** The binding constraint is not signal quality, it
is cost: median cost was 0.385 R per trade, because an 0.08% round trip against
a stop 0.2% away eats 40% of the risk. Trades with stops under 0.15% were
+0.180 R *gross* and −0.580 R *net*.

A high score means many conditions agree right now. It never means the trade
wins. At 100-400x, sizing and risk decide the outcome far more than the signal
does.

Re-run the sweep yourself with `scratchpad/threshold_sweep.py`.
