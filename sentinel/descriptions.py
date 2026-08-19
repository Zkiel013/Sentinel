"""Human-readable documentation for every detector, score component and zone.

Kept in one place and served over the API so the UI never hard-codes copy —
add a detector, add its entry here, and its detail panel appears by itself.

Each setup entry:
    short  one-line gist
    what   the mechanical trigger condition
    why    the market reason people trade it
    how    typical execution on 1-5m
    fails  the known failure mode
    tf     which timeframes it is meaningful on
"""

from __future__ import annotations

SETUPS: dict[str, dict] = {
    # ---------------- retail scalping playbook ----------------
    "ema_cross_pullback": {
        "title": "EMA 9/21 cross + pullback",
        "short": "The most-used crypto scalp: fast/slow EMA regime, enter on the retest.",
        "what": "EMA9 crossed EMA21 within the last 20 bars, price then pulled back "
                "into the EMA9–EMA21 band and closed back on the trend side of EMA9.",
        "why": "The cross defines which side of the market is in control. Chasing the "
               "cross bar pays the worst price of the move; the first pullback into "
               "the band is where continuation buyers sit, so risk is small and "
               "measurable against the band.",
        "how": "Enter on the close that reclaims EMA9. Stop just beyond the far side "
               "of the EMA band (or 1x ATR). Target the prior swing; trail EMA9 if it "
               "extends. Strength decays with bars since the cross — 20+ bars old and "
               "the edge is gone.",
        "fails": "Chop. In a range EMA9/21 cross back and forth every few bars and "
                 "every pullback is a loss. Filter with EMA200 or ADX-style regime.",
        "tf": "1m, 5m, 15m — strongest on 5m for BTC/ETH.",
    },
    "vwap_bounce": {
        "title": "VWAP bounce",
        "short": "Session VWAP as the intraday line in the sand; trade the rejection off it.",
        "what": "Price is on one side of session VWAP, the current bar's wick tagged "
                "VWAP (within 0.3x ATR) and it closed back in the original direction.",
        "why": "VWAP is where the day's average filled participant sits. Desks and "
               "algos benchmark to it, so it attracts real resting orders rather than "
               "being a drawn line. Above VWAP the average buyer is in profit and "
               "defends; below it they are underwater and sell rallies.",
        "how": "Limit order at VWAP with the trend, stop 0.5–1x ATR beyond it. "
               "Invalidated the moment price closes through and holds the other side "
               "— that is a regime change, not a deeper dip.",
        "fails": "Trendless days pin price to VWAP and it gets tagged twenty times; "
                 "and VWAP resets at 00:00 UTC, so the first hour of a session it "
                 "carries almost no information.",
        "tf": "1m, 5m, 15m, 1h. Meaningless above 1h (VWAP is a session tool).",
    },
    "liquidity_grab": {
        "title": "Liquidity grab / stop hunt",
        "short": "An obvious swing gets swept, then the close rejects it.",
        "what": "The bar traded through the last confirmed swing high/low but closed "
                "back inside it, leaving a wick larger than 0.45x ATR.",
        "why": "Stops cluster right beyond obvious highs and lows. Price is pulled "
               "there because that is where the resting liquidity is, fills the size, "
               "then leaves. The sweep is the fill; the rejection close is the tell "
               "that it was liquidity-seeking rather than a real break.",
        "how": "Enter on the close of the rejection bar, or on the retest of the swept "
               "level. Stop beyond the wick extreme — that wick is the invalidation, "
               "and it is usually tight, which is why the pattern is popular at size.",
        "fails": "The sweep is genuine and price never comes back — indistinguishable "
                 "in real time from a real break. Deep books (BTC, ETH) reject more "
                 "often than thin alts, which is why it works better on majors.",
        "tf": "All. Cleanest on 1m/5m where stop clusters are visible.",
    },
    "rsi_sr_confluence": {
        "title": "RSI + support/resistance confluence",
        "short": "RSI alone is noise; at a pivot level it becomes a location filter.",
        "what": "RSI(14) below 36 while the bar's low is within 0.45x ATR of a prior "
                "pivot low (or above 64 at a prior pivot high).",
        "why": "Oversold is not a reason to buy — trends stay oversold for hours. "
               "Oversold *at a level people already defended* means momentum is "
               "exhausted exactly where resting bids sit. The level is the edge; RSI "
               "only times it.",
        "how": "Wait for the bar at the level to close, then enter. Stop below the "
               "level, not below RSI. If price closes decisively through the level "
               "the setup is void regardless of what RSI reads.",
        "fails": "Strong trends. In a real downtrend every support breaks and RSI "
                 "stays pinned low; this setup will hand you every knife.",
        "tf": "5m, 15m, 1h. On 1m the RSI thresholds trigger constantly.",
    },
    "breakout_retest": {
        "title": "Breakout + retest",
        "short": "Skip the naked break, take the retest. Fakeouts die on the retest.",
        "what": "A pivot level was decisively closed through in the last ~20 bars, "
                "price has now returned to within 0.35x ATR of it and closed back on "
                "the breakout side.",
        "why": "Most crypto breakouts on low timeframes are liquidity sweeps. Demanding "
               "a retest filters them: a fake break cannot reclaim the level, a real "
               "one turns old resistance into new support because trapped sellers "
               "cover there.",
        "how": "Enter on the retest hold. Stop just the other side of the level — the "
               "tightest stop the pattern offers. Target: the measured move (range "
               "height projected from the break).",
        "fails": "The best breakouts never retest, so you miss them. And a retest that "
                 "keeps going through is a failed break — cut immediately, do not "
                 "average.",
        "tf": "5m, 15m, 1h.",
    },
    "bb_squeeze": {
        "title": "Bollinger squeeze expansion",
        "short": "Volatility contracts, then expands. The squeeze is the setup; the "
                 "expansion bar is the trigger.",
        "what": "Bollinger band width sat in the bottom 22% of its own 120-bar history, "
                "then widened by 25%+ while price closed outside the prior band.",
        "why": "Volatility is mean-reverting even when price is not. Compression means "
               "positioning is building with no resolution; the expansion is that "
               "positioning being forced to pick a side. Direction is taken from the "
               "break, never guessed during the squeeze.",
        "how": "No position during the squeeze. Enter on the expansion close, stop at "
               "the opposite band, target 2x the squeeze range. Percentile-based, so "
               "it self-scales across regimes instead of using a fixed width.",
        "fails": "The first expansion bar is often the whole move — you buy the high. "
                 "And squeezes can expand, fail, and re-compress twice before the "
                 "real move.",
        "tf": "5m, 15m, 1h. Needs 130+ bars of history to have a percentile.",
    },

    # ---------------- original detector set ----------------
    "orb": {
        "title": "Opening range breakout",
        "short": "Close breaks the first 30 minutes' high/low of the UTC session.",
        "what": "Latest close crossed above the 30-minute opening range high, or below "
                "its low, having been inside on the prior bar.",
        "why": "The opening range is where the session's first real two-sided auction "
               "happens. Breaking it means one side won the auction and the day likely "
               "trends from there.",
        "how": "Enter on the breaking close, stop at the opposite side of the range, "
               "target 1x the range height.",
        "fails": "Crypto has no true daily open — 24/7 markets make the UTC session "
                 "boundary arbitrary, so the range is much weaker than in equities.",
        "tf": "1m, 5m, 15m.",
    },
    "vwap_fade": {
        "title": "VWAP fade (2σ stretch)",
        "short": "Price stretched two or more standard deviations from session VWAP.",
        "what": "Close is beyond the session VWAP ±2σ band.",
        "why": "Mean reversion. Two sigma from the session's volume-weighted average "
               "is a statistically stretched price; market makers lean against it.",
        "how": "Counter-trend, so size down. Target VWAP itself, stop beyond 3σ.",
        "fails": "Trend days ride the 2σ band for hours and every fade loses. Opposite "
                 "of `vwap_bounce` — check which regime you are in first.",
        "tf": "1m, 5m, 15m, 1h.",
    },
    "ema_pullback": {
        "title": "EMA20 pullback in trend",
        "short": "Trending versus EMA200, price touched EMA20 and held.",
        "what": "Close above EMA200 with the bar's low touching EMA20 and closing back "
                "above it (mirrored for downtrend).",
        "why": "Classic trend continuation: the EMA20 is the shallow-pullback zone in "
               "an established trend, with EMA200 as the regime filter.",
        "how": "Enter on the hold, stop 1x ATR below EMA20, trail the EMA.",
        "fails": "Late-trend pullbacks turn into reversals; EMA200 lags badly after a "
                 "regime change.",
        "tf": "All.",
    },
    "rsi2_extreme": {
        "title": "RSI(2) extreme",
        "short": "2-period RSI below 5 (washed out) or above 95 (overheated).",
        "what": "RSI(2) < 5 or > 95 on the closed bar.",
        "why": "A very short RSI measures immediate exhaustion rather than trend, so "
               "extremes mark short-term capitulation.",
        "how": "Mean-reversion entry, exit fast (1-3 bars). Needs a level or VWAP "
               "confluence to be worth taking.",
        "fails": "Fires constantly on 1m and stays pinned during real trends.",
        "tf": "5m, 15m, 1h.",
    },
    "funding_extreme": {
        "title": "Funding rate extreme",
        "short": "Perp funding beyond its own 90-day 90th/10th percentile.",
        "what": "Current funding rate above the 90th percentile (crowded longs, bearish) "
                "or below the 10th (crowded shorts, bullish) of its 90-day history.",
        "why": "Funding is the price of leverage. When one side pays heavily to hold, "
               "that side is crowded and is the side that gets liquidated first.",
        "how": "Positioning context, not a trigger. Use it to pick which direction's "
               "signals you act on.",
        "fails": "Funding can stay extreme through an entire trend leg. Percentile-based "
                 "here rather than a fixed threshold, which helps but does not fix it.",
        "tf": "All — funding is a symbol-level fact, not a timeframe one.",
    },
    "liquidation_cascade": {
        "title": "Liquidation cascade",
        "short": "Five or more forced liquidations and over $2M notional inside 60s.",
        "what": "Binance futures forceOrder stream shows a cluster of liquidations in "
                "the last minute.",
        "why": "Forced selling is price-insensitive: it overshoots, then snaps back "
               "once the queue clears. The snap-back is the trade.",
        "how": "Wait for the cascade to stop printing before entering — catching the "
               "middle of one is how accounts die. Fast target, tight time stop.",
        "fails": "Cascades chain: one triggers the next liquidation band down. There is "
                 "no way to know from the stream which one is the last.",
        "tf": "1m, 5m.",
    },
    "volume_spike": {
        "title": "Volume spike",
        "short": "Volume three or more standard deviations above its 48-bar average.",
        "what": "Volume z-score > 3 on the closed bar; direction taken from the bar's "
                "own return.",
        "why": "Confirmation, not a signal. Real moves need participation; a break on "
               "no volume is usually noise.",
        "how": "Use as a filter on top of another setup. Alone it is directionless.",
        "fails": "Spikes also mark exhaustion tops and bottoms — same reading, opposite "
                 "meaning.",
        "tf": "All.",
    },
    "session_open_volatility": {
        "title": "Session-open volatility",
        "short": "Unusually large bar within 30 minutes of the UTC, London or NY open.",
        "what": "Bar range greater than 2x ATR inside a session-open window.",
        "why": "Liquidity and participation step-change at these times, which is when "
               "ranges break and stops get run.",
        "how": "Regime warning rather than a direction: widen stops, expect follow-through.",
        "fails": "Direction-neutral by design, so it cannot be traded on its own.",
        "tf": "1m, 5m, 15m.",
    },
    "structure_break": {
        "title": "Market structure break (BOS)",
        "short": "Close beyond the last confirmed swing high/low.",
        "what": "Close crossed the most recent confirmed fractal pivot.",
        "why": "The definition of trend change on price alone: higher highs stop, or a "
               "prior high is taken. No indicator lag.",
        "how": "Enter on the break close or its retest, stop beyond the origin of the "
               "move.",
        "fails": "Pivot confirmation lags by several bars, so on 1m the break is often "
                 "already extended by the time it is confirmed.",
        "tf": "All.",
    },
    "fvg": {
        "title": "Fair value gap",
        "short": "Three-candle imbalance larger than half an ATR.",
        "what": "Bar -3's high is below bar -1's low (or inverse) by more than 0.5x ATR, "
                "leaving an untraded pocket.",
        "why": "Price moved so fast that a price band never auctioned. Those pockets "
               "often get revisited to fill the missing volume.",
        "how": "Limit order inside the gap, expecting a fill and continuation.",
        "fails": "Plenty of gaps never fill; strong displacement means the gap is a "
                 "trend footprint, not a magnet.",
        "tf": "All.",
    },
    "order_block": {
        "title": "Order block",
        "short": "Displacement candle right after an opposite candle marks the zone.",
        "what": "A body larger than 2x ATR immediately after an opposite-direction "
                "candle; that prior candle's range is the block.",
        "why": "The displacement implies a large participant filled at the prior "
               "candle's price. That range is treated as unfilled demand or supply.",
        "how": "Limit order at the block edge, stop beyond the block.",
        "fails": "Highly discretionary — which candle counts as the block is a choice, "
                 "and the concept has weak statistical support.",
        "tf": "All.",
    },
    "sr_reaction": {
        "title": "Support/resistance reaction",
        "short": "Rejection wick off a recent pivot level.",
        "what": "The bar's wick came within 0.5x ATR of one of the last five pivots and "
                "closed away from it.",
        "why": "Levels that produced reversals attract resting orders and often produce "
               "another.",
        "how": "Enter on the rejection close, stop beyond the level.",
        "fails": "Every level eventually breaks, and the third or fourth test is the one "
                 "that usually goes.",
        "tf": "All.",
    },
    "trend_continuation": {
        "title": "Trend continuation (stacked EMAs)",
        "short": "EMAs stacked 20>50>200 (or inverse) and the pullback resolved.",
        "what": "Stacked EMA order, prior bar pulled into EMA20, current bar closed "
                "beyond the prior bar's extreme.",
        "why": "Stacked EMAs are the cleanest definition of an intact trend; the resolved "
               "pullback is the entry.",
        "how": "Enter on the resolution close, trail EMA20.",
        "fails": "Stacking is a lagging condition — it is most perfect right before "
                 "mean reversion.",
        "tf": "All.",
    },
    "mean_reversion": {
        "title": "Bollinger mean reversion",
        "short": "Close back inside the band after a 2σ excursion.",
        "what": "Prior bar's z-score was beyond ±2 and the current close came back "
                "inside.",
        "why": "The re-entry, not the excursion, is the signal that the stretch is over.",
        "how": "Target the 20-period mid band, stop beyond the excursion extreme.",
        "fails": "In trends price walks the band and each re-entry is a losing counter-"
                 "trend entry.",
        "tf": "All.",
    },
}

# ---------------- score components ----------------

COMPONENTS: dict[str, dict] = {
    "mtf_trend": {
        "title": "Higher-timeframe trend",
        "what": "Trend direction on every timeframe above the one you are viewing, "
                "weighted so 4h counts more than 15m.",
        "why": "A 1m long against a falling 1h is a fade, not a trend trade. This is "
               "the single biggest reason low-timeframe scalps fail.",
        "read": "Positive means the higher timeframes are pointing up. If this "
                "disagrees with your direction, the trade needs a tighter stop and a "
                "faster target.",
    },
    "trend_local": {
        "title": "Local trend structure",
        "what": "EMA 9/21/50/200 ordering and price position on the viewed timeframe.",
        "why": "Defines whether the current timeframe is trending or ranging, which "
               "decides whether continuation or reversion setups are the right tool.",
        "read": "Near zero means chop — a signal that mean-reversion setups are "
                "preferable and breakout setups are traps.",
    },
    "momentum": {
        "title": "Momentum",
        "what": "RSI(14) distance from 50 plus the 3-bar rate of change.",
        "why": "Separates a move that is being pushed from one that is drifting.",
        "read": "Strong momentum against your direction is the main reason to wait for "
                "one more bar.",
    },
    "structure": {
        "title": "Position within structure",
        "what": "Where price sits relative to the nearest dynamic support and "
                "resistance zones, scaled by those zones' strength.",
        "why": "Location decides risk. Buying directly under major resistance is a bad "
               "trade even with perfect momentum, because the stop has to be wide and "
               "the target is close.",
        "read": "Positive means there is more room above than below — favourable for "
                "longs. Strongly negative means you are buying into a wall.",
    },
    "flow": {
        "title": "Order flow",
        "what": "Volume z-score, taker-buy share of volume, and liquidation side skew "
                "in the last minute.",
        "why": "Confirms whether a price move has real participation behind it or is "
               "just a thin drift that will be given back.",
        "read": "Flow agreeing with price is confirmation. Price up on falling volume "
                "with sellers taking the offer is distribution.",
    },
    "funding": {
        "title": "Funding / positioning",
        "what": "Current perpetual funding rate versus its own 90-day distribution.",
        "why": "The crowded side pays to stay in and is the side that gets liquidated. "
               "Read contrarian.",
        "read": "Positive contribution means the crowd is leaning the other way from "
                "your direction, which is where the fuel is.",
    },
    "volatility": {
        "title": "Volatility regime",
        "what": "ATR as a percentage of price, plus Bollinger width percentile.",
        "why": "At 100-400x leverage volatility *is* your risk. It does not pick a "
               "direction, it scales how much the rest of the score can be trusted "
               "and how wide your stop has to be.",
        "read": "This one damps the final score rather than pushing it either way. "
                "High ATR% with a squeeze percentile means an expansion is coming and "
                "the current reading is fragile.",
    },
    "setups": {
        "title": "Detected setups",
        "what": "Every detector that fired on this closed bar, weighted by its own "
                "reliability and strength.",
        "why": "The concrete, nameable patterns behind the number — this is what you "
               "would actually point at on the chart.",
        "read": "Conflicting setups on the same bar cut the score. Two agreeing setups "
                "from different categories are worth more than three from the same one.",
    },
    "vwap": {
        "title": "VWAP position",
        "what": "Distance from session VWAP measured in ATR.",
        "why": "VWAP is the intraday fair-value anchor and where the day's average "
               "participant breaks even.",
        "read": "Small positive is healthy trend. Beyond about 2x ATR is stretched, and "
                "the reversion risk starts to outweigh the trend.",
    },
}

ZONES_DOC = {
    "title": "Dynamic support & resistance zones",
    "what": "Confirmed swing pivots are clustered into price *bands* (not lines) "
            "whenever they sit within 0.45x ATR of each other. Every band is scored on "
            "how many times price visited it, how often it rejected from it, the volume "
            "on those visits, which timeframe produced it, and how recently it was "
            "touched.",
    "why": "Real levels are zones, because the orders behind them are spread over a "
           "price range. A single line gives false precision and a stop that gets "
           "wicked out.",
    "dynamic": "Zones are rebuilt from live candles on every closed bar, so they widen, "
               "merge, decay and disappear on their own. Nothing is drawn by hand and "
               "nothing is fixed.",
    "mtf": "Zones are built per timeframe and then merged, highest timeframe first. A "
           "4h zone absorbs overlapping 15m and 5m zones and gains strength from the "
           "confluence. When a higher-timeframe zone is decisively broken, every "
           "lower-timeframe zone nested inside it is demoted to 35% strength and "
           "flagged as invalidated — the 1m structure inside a 1h level stops mattering "
           "once that 1h level gives way. That is what makes the rectangles "
           "self-adjust instead of accumulating forever.",
    "tiers": "Major = strength 0.62+, medium = 0.38+, minor below. A broken zone is "
             "drawn as a flip zone (old support acting as resistance) at reduced "
             "strength, and is discarded entirely once price is 14 ATR away from it.",
    "roles": "support = below price · resistance = above price · inside = price is "
             "currently trading in the band · flip = broken, now acting from the other "
             "side.",
}

SCORE_DOC = {
    "title": "How the 1-100 score is built",
    "what": "Nine weighted components each produce a signed reading from -1 (fully "
            "bearish) to +1 (fully bullish). Their weighted average is the net "
            "direction. The headline score combines the size of that net reading, how "
            "many components agree with it, and a quality term that penalises chop, "
            "extreme volatility and conflicting signals.",
    "buysell": "buy_score and sell_score always sum to 101 and are the same reading "
               "from either side — a buy_score of 72 is a sell_score of 29. The "
               "headline `score` is different: it is the conviction in the recommended "
               "action, which is why it can be low even when the direction is clear "
               "(clear direction, bad location, or a volatility regime that makes the "
               "stop unusable).",
    "action": "buy or sell needs net direction past ±0.12 *and* a score of 35+. "
              "Everything else is wait — most bars are wait, and that is the correct "
              "output for most bars.",
    "honest": "This is a measure of how many independent conditions agree right now. "
              "It is not a probability, not an expected return, and not advice. "
              "Backtests in this same repo showed these setups lose money after "
              "realistic fees, and agreement between indicators does not fix that.",
}


TIMING_DOC = {
    "title": "Session quality — when is it worth trading",
    "what": "A 0-100 score for the current hour, built from this symbol's own "
            "90-day hourly history. Every threshold is a percentile inside that "
            "symbol's own distribution, so it self-scales rather than relying on "
            "fixed volume numbers.",
    "components": "Four parts. **Liquidity** (32%) uses trade *count*, not "
                  "turnover — one whale print inflates volume without adding "
                  "participants, and it is participant breadth that tightens the "
                  "spread. **Range** (24%) asks whether a typical hour moves "
                  "enough to clear fees and spread, and is scored against cost "
                  "rather than in absolute terms; too much range is penalised too, "
                  "because it makes a scalp stop unusable. **Efficiency** (22%) is "
                  "|close−open| / (high−low) — whether the hour trends or chops. "
                  "**Integrity** (22%) is the inverse of thin-and-wicky.",
    "why_not_volume": "Volume alone would score the wrong hours as best. On BTC, "
                      "19:00-21:00 IST carries roughly triple the trade count of "
                      "02:00 IST but runs *worse* efficiency (0.35-0.42) and more "
                      "wick (60%+). The busiest hours are the whippiest; the "
                      "cleanest directional hours are quieter ones like 08:00 and "
                      "10:00 IST. That is why efficiency and integrity are scored "
                      "separately from liquidity.",
    "manipulation": "A low trade count combined with a high wick share is the "
                    "signature of stop-hunting in a shallow book — price reaching "
                    "for resting liquidity because there is nothing else there. "
                    "The integrity component is that condition inverted, so hours "
                    "where it happens score down. At 100-400x this is the "
                    "difference between a stop being hit by information and being "
                    "hit by nothing at all.",
    "live": "The base score is the historical profile for the current IST hour. "
            "It is then adjusted for what is actually happening: if this hour is "
            "running below about 55% of its own normal volume the score is cut, "
            "because a normally-prime hour that nobody showed up for is a thin "
            "book wearing a good reputation. Funding settlement windows "
            "(00:00 / 08:00 / 16:00 UTC, ±15 min) and the 00:00 UTC daily close "
            "are also marked down — movement there is positioning, not direction.",
    "weekend": "Weekday and weekend profiles are built separately, because "
               "weekend crypto is a different market: on BTC roughly half the "
               "trade count and half the range. Weekend hours therefore score far "
               "lower across the board, and that is measured, not assumed.",
    "tiers": "prime 72+ · good 58+ · fair 44+ · poor 30+ · avoid below 30. "
             "Windows shown in the panel are contiguous runs of hours scoring "
             "'good' or better; single-hour runs are dropped as sampling noise.",
    "honest": "This describes when the market is liquid and orderly. It says "
              "nothing about direction and does not make a losing strategy "
              "profitable — a good session only means your costs and slippage are "
              "closer to what the trade plan assumed.",
}


GATE_DOC = {
    "title": "The trade gate — what has to clear before it says buy or sell",
    "what": "A set of minimums. A closed bar has to pass every one of them before "
            "the Analysis tab will call it a buy or a sell; anything failing is "
            "reported as **wait**, with the specific gate and the value that "
            "failed. Nothing is hidden — a suppressed signal still shows what it "
            "wanted to do and why it was held back.",
    "how_chosen": "By replay, not by feel. `threshold_sweep.py` re-runs this exact "
                  "scoring engine bar by bar over roughly 1400 bars of 5m and 15m "
                  "history on BTC and ETH, takes the trade plan the engine would "
                  "have produced at that bar, and walks forward to see whether the "
                  "stop or the first target came first. Every outcome is expressed "
                  "in R and charged an 0.08% round trip, so the numbers are net of "
                  "costs. The gate defaults are the thresholds where measured "
                  "expectancy was least bad.",
    "no_lookahead": "The score and plan at each bar are computed from frames "
                    "truncated to that bar; the outcome walk begins on the next "
                    "bar. Limit entries only count if they filled within three "
                    "bars, and when a bar contains both the stop and the target "
                    "the stop is assumed — the conservative read.",
    "gates": "**Min score** is headline conviction. **Min directional lean** is "
             "|net| — below it the two sides are too close to call. **Min "
             "agreement** is the fraction of scoring components pointing the same "
             "way. **Min reward:risk** filters bad location: right direction, but "
             "the first target is too close to the stop to be worth it. **Min "
             "session quality** is the Timing score, which blocks thin hours where "
             "a shallow book gets pushed through levels. **Block squeezes** refuses "
             "to act while volatility is compressed and the direction is undecided.",
    "measured": "What the replay found, on 817 filled trades. No gate — 817 trades "
                "at **−0.404 R** average. Min score 50 — 308 trades at **−0.267 R**, "
                "better in all four symbol/timeframe runs. Min score 50 plus min "
                "reward:risk 1.8 plus block-squeezes — 229 trades at **−0.189 R**, "
                "again better in all four runs. That last combination is the "
                "shipped default: it removes about 72% of signals and roughly "
                "halves the average loss.",
    "rejected": "Three things looked good and were thrown out. **Min score 65** was "
                "the best row in the pooled table at −0.054 R, but it improved in "
                "only one of the two runs that had enough samples and was −0.864 R "
                "on ETH 15m — a curve fit to 43 trades. **Min agreement** had no "
                "consistent sign: pooled it helped, but on ETH 15m the "
                "highest-agreement bucket was the single worst group, so it is left "
                "ungated. **Requiring a wider stop** made results worse, not "
                "better — tight stops carry a positive edge *before* costs and lose "
                "all of it to the round trip, while wider stops have no edge to "
                "begin with. There is no stop distance at which this nets out.",
    "costs": "The real problem is not the threshold, it is the cost-to-risk ratio. "
             "Median cost was **0.385 R per trade** — an 0.08% round trip against "
             "stops that sit 0.13-0.25% from entry. Trades with stops under 0.15% "
             "were **+0.180 R gross and −0.580 R net**. The signal is not the "
             "binding constraint; the spread is.",
    "raising": "Raising a gate always cuts the number of trades and usually raises "
               "the win rate, and those two do not have to net out in your favour. "
               "Watch total R across the whole sample, not the win rate: a gate "
               "that turns 200 trades into 12 can look excellent and still be "
               "curve-fitted to a handful of bars.",
    "honest": "A threshold cannot manufacture an edge that is not in the signal. "
              "The backtester in this repo found these setups lose money after "
              "realistic costs, and the replay behind these defaults is a single "
              "recent sample on two symbols — it is enough to rank thresholds "
              "against each other, not enough to prove any of them profitable. "
              "Treat the gate as a discipline tool that stops you trading the worst "
              "bars, not as a validated edge.",
}


def all_docs() -> dict:
    return {"setups": SETUPS, "components": COMPONENTS, "zones": ZONES_DOC,
            "score": SCORE_DOC, "timing": TIMING_DOC, "gate": GATE_DOC}
