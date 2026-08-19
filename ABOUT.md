# Sentinel — descriptions

Copy for repo fields, portfolios and docs. Every version below is accurate to
what the code does. That accuracy is the point: describing it as a measurement
tool is not a softening of the truth, it *is* the truth, and an accurate
description is what keeps it from reading as an advisory service.

---

## One line (GitHub description, ~120 chars)

> Personal market-analysis dashboard: detects technical conditions on live crypto
> data and scores how many of them agree.

Alternates:

> Self-hosted technical-analysis dashboard for crypto price data. Personal
> research tool.

> Indicator engine and charting dashboard that quantifies agreement between
> technical conditions.

---

## Short paragraph (~350 chars, for a repo About or a portfolio card)

> Sentinel is a self-hosted dashboard for studying live crypto price data. It
> computes standard technical indicators, flags when recognised chart patterns
> appear, clusters swing pivots into support and resistance bands, and scores how
> many independent conditions currently agree with each other. Built as a personal
> research and study tool.

---

## Full description

> Sentinel is a self-hosted analysis dashboard for crypto price data, built for
> personal study of technical-analysis methods.
>
> It reads public market data, computes a standard indicator set (EMA, RSI, ATR,
> VWAP, Bollinger bands, volume z-score), and flags when any of twenty documented
> chart patterns appear on a closed candle. Swing pivots are clustered into price
> bands and scored on how often price has respected them. A composite score
> quantifies how many independent technical conditions currently point the same
> way, which is a measure of agreement between indicators — not a forecast.
>
> The dashboard also profiles historical hourly liquidity and volatility so a
> session can be characterised as active or thin, and includes an arithmetic
> calculator for position size, margin and liquidation distance at a given
> leverage.
>
> Every reading is derived from closed candles and displayed as-is. Separate
> backtesting of the implemented patterns found them unprofitable after realistic
> transaction costs; that result is documented in the repository rather than
> omitted.
>
> Stack: Python, FastAPI, pandas, SQLite, vanilla JS. No account, no API key, no
> external service. Runs locally.

---

## Disclaimer block

Put this near the top of anything public.

> Sentinel is a personal research and study tool. It performs technical
> calculations on public market data and displays the results. It does not
> provide investment advice, recommendations, or predictions, is not a financial
> product or service, and is not offered to or operated on behalf of any other
> person. Nothing it outputs should be treated as a reason to enter a
> transaction. Trading leveraged instruments carries a high risk of loss.

---

## Word choices that matter

The framing risk is not in the feature list; it is in verbs. Two descriptions of
identical software can read very differently.

| Reads as advisory | Reads as analytical |
|---|---|
| tells you when to buy | flags when a condition occurs |
| trading signals | pattern detections |
| recommends a trade | displays a computed reading |
| entry and exit advice | reference levels derived from pivots |
| profitable setups | documented chart patterns |
| predicts direction | measures agreement between indicators |
| bot / auto-trader | dashboard / monitor |
| for traders | for personal research |

Avoid entirely: *guaranteed, returns, profit, win rate, beat the market, proven,
risk-free, signals service, subscribe.* Performance claims are the single
strongest trigger, and none of them would be true here anyway.

Also: the tool places no orders and holds no credentials. Saying so plainly is
worth more than any careful phrasing — an execution path is what changes the
category of the software.

---

## Note on the interface

The README is the easy part. The dashboard currently prints **BUY** and **SELL**
as the headline verdict and labels a panel **Advice** — which reads more
directive than the surrounding disclaimers. If the description above is meant to
match the product, the UI wording is the part that actually needs changing:

| Current | Neutral equivalent |
|---|---|
| `BUY` / `SELL` | `LONG BIAS` / `SHORT BIAS` |
| `Advice` | `Reading` or `Interpretation` |
| `What to do` | `What the conditions show` |
| `Trade plan` | `Reference levels` |

These are label changes only; no logic is affected.
