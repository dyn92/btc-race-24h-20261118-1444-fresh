# Slow mean-reversion long-only bot for cbBTC (CBBTC/USDC, 1-minute bars).
#
# Why this shape: in these 24h replay races a full-size round trip costs
# roughly 26 bps (fees plus pool impact on an all-in position), so any
# system that trades often donates its edge to the pool. Fast momentum and
# EMA-cross variants churn themselves to death; wide breakout triggers
# never fire. What survives is buying genuine dislocations under a slow
# mean and selling the snap back to it:
#
#   Entry: flat, and the latest close sits at or below EMA(180) - DIP_BPS.
#   Exit:  the close recovers to EMA(180) + RECOVER_BPS, or the position
#     falls STOP_FRAC under its cost basis, or MAX_HOLD_BARS pass --
#     whichever comes first.
#   After an exit the bot waits REENTRY_COOLDOWN_BARS before re-entering:
#   a dislocation often deepens once more right after the first snap
#   back, and the pause buys a better re-entry.
#   No new position is opened in the last NO_ENTRY_LAST_BARS bars of the
#   window: whatever is held at the end is flattened at the last close,
#   so a late entry has no time to recover and only donates its opening
#   drawdown.
#
# One position at a time, all-in/all-out, so the trade count stays in
# single digits and costs stay a small share of the moves harvested.
# Exits are level-based: an order the market refuses (slippage cap) is
# simply sent again on the next batch while the level holds. The position
# and its cost basis are read back from the account on every batch, so a
# rejected fill can never desync the state.
#
# Deterministic: float64 arithmetic on closes only; no clock, no
# randomness, no network, no files. Pure Python standard library.
# A position still open at the last batch is flattened by the protocol
# itself (done message), so on_batch never needs an end-of-window sell.

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
EMA_SPAN = 180  # slow mean: 3 hours of 1-minute closes
DIP_BPS = 60  # entry: close <= EMA * (1 - 60bps)
RECOVER_BPS = 40  # exit: close >= EMA * (1 + 40bps)
STOP_FRAC = 0.02  # exit: -2% vs cost basis
MAX_HOLD_BARS = 720  # exit: 12 hours in the position
REENTRY_COOLDOWN_BARS = 60  # bars to sit out after an exit
MAX_SLIPPAGE_BPS = 200  # 2% past the close the order was decided on
# Optional guards (0 / False = off; set by tuning, see README):
TREND_LOOKBACK_BARS = 0  # bars over which the EMA's own move is measured
TREND_MAX_DROP_BPS = 0  # skip entries when the EMA fell more than this
REQUIRE_UPTICK = False  # enter only on a bar that closed up vs the prior bar
NO_ENTRY_LAST_BARS = 360  # never open a position this close to the window's end
WINDOW_BARS = 1440  # the race is 24h of 1-minute bars

closes = []
ema = None
ema_warm = []
ema_hist = []
entry_seq = -1
last_exit_seq = -1
had_position = False
scored_seen = 0


def step_ema(close):
    global ema
    if ema is not None:
        ema += (2.0 / (EMA_SPAN + 1)) * (close - ema)
        ema_hist.append(ema)
        return
    ema_warm.append(close)
    if len(ema_warm) == EMA_SPAN:
        ema = sum(ema_warm) / EMA_SPAN
        ema_hist.append(ema)


def on_batch(state, batch):
    global entry_seq, last_exit_seq, had_position, scored_seen
    sym = state.symbols.get(SYMBOL)
    if sym is None:
        return []
    # Fold this batch's CBBTC bar (if it carries one) into the indicators.
    for bar in batch["bars"]:
        if bar["symbol"] != SYMBOL:
            continue
        close = int(bar["c"]) / 10 ** sym["price_scale"]
        closes.append(close)
        step_ema(close)
    if state.warming_up:
        return []
    scored_seen += 1

    held = state.positions.get(SYMBOL)
    if held is not None:
        had_position = True
        if entry_seq < 0:
            entry_seq = state.seq
        last = closes[-1] if closes else None
        ret = state.value(SYMBOL, held.qty) / held.cost - 1 if held.cost else 0.0
        dev_bps = (last / ema - 1) * 10000 if ema is not None and last is not None else 0.0
        if dev_bps >= RECOVER_BPS or ret <= -STOP_FRAC or state.seq - entry_seq >= MAX_HOLD_BARS:
            entry_seq = -1
            return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    # Flat: note the batch on which the last position disappeared, then
    # wait out the re-entry cooldown before buying the next dislocation.
    if had_position:
        had_position = False
        last_exit_seq = state.seq
    entry_seq = -1
    if last_exit_seq >= 0 and state.seq - last_exit_seq < REENTRY_COOLDOWN_BARS:
        return []
    if ema is None or not closes:
        return []
    last = closes[-1]
    dev_bps = (last / ema - 1) * 10000
    if dev_bps > -DIP_BPS:
        return []
    # Guards: in a persistently falling market a dip under the mean is a
    # falling knife, not a dislocation; and a position opened near the
    # window's end has no time to recover before the forced flatten.
    if TREND_MAX_DROP_BPS and len(ema_hist) > TREND_LOOKBACK_BARS:
        trend_bps = (ema_hist[-1] / ema_hist[-1 - TREND_LOOKBACK_BARS] - 1) * 10000
        if trend_bps < -TREND_MAX_DROP_BPS:
            return []
    if REQUIRE_UPTICK and len(closes) >= 2 and closes[-1] < closes[-2]:
        return []
    if NO_ENTRY_LAST_BARS and scored_seen > WINDOW_BARS - NO_ENTRY_LAST_BARS:
        return []
    min_notional = int(state.hello["fees"]["min_notional_quote_units"])
    if state.cash < min_notional:
        return []
    return [{"side": "buy", "symbol": SYMBOL, "notional_quote_units": state.cash,
             "max_slippage_bps": MAX_SLIPPAGE_BPS}]


if __name__ == "__main__":
    makefun_bars.run(on_batch)
