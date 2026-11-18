# cbBTC mean-reversion bot, fresh implementation.
#
# The replay charges about 26 bps for a full-size round trip (fees plus
# the pool impact of an all-in fill), so frequent trading is a donation
# to the pool. This bot therefore waits for a real dislocation and then
# trades it once, all-in and all-out:
#
#   - Flat and the latest close is at least DIP_BPS under the slow mean:
#     buy with all the cash.
#   - Holding and the close is back at least RECOVER_BPS over the slow
#     mean, or the position is STOP_PCT down against its cost, or it has
#     been held MAX_HOLD bars: sell everything.
#   - After any exit, sit out COOLDOWN bars before the next entry: the
#     first snap back is often followed by one more dip.
#   - Never open in the last NO_ENTRY bars: the protocol flattens
#     whatever is held at the last close, and a late entry would only
#     donate its opening drawdown.
#
# The slow mean is an EMA over three hours of one-minute closes. All
# state (mean, close history, entry bar, last exit bar) is read back
# from the account on every batch, so a rejected order can never leave
# the bot's picture of the position out of sync with the harness's.
# Nothing here reads the clock, the network, or a file, and there is no
# randomness: the same bars always produce the same orders.

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
SPAN = 180
DIP_BPS = 60
RECOVER_BPS = 40
STOP_PCT = 2
MAX_HOLD = 720
COOLDOWN = 60
NO_ENTRY = 360
WINDOW = 1440
SLIPPAGE = 200


class SlowMean:
    """EMA over closes, seeded with the plain mean of the first SPAN."""

    def __init__(self, span):
        self.span = span
        self.alpha = 2.0 / (span + 1)
        self.seed = []
        self.value = None
        self.past = []

    def add(self, close):
        if self.value is None:
            self.seed.append(close)
            if len(self.seed) < self.span:
                return None
            total = 0.0
            for c in self.seed:
                total += c
            self.value = total / self.span
            self.past.append(self.value)
            return self.value
        self.value = self.value + self.alpha * (close - self.value)
        self.past.append(self.value)
        return self.value


class Trader:
    def __init__(self):
        self.mean = SlowMean(SPAN)
        self.history = []
        self.entry_bar = -1
        self.exit_bar = -1
        self.was_holding = False
        self.seen = 0

    def decide(self, state, batch):
        sym = state.symbols.get(SYMBOL)
        if sym is None:
            return []
        scale = 10 ** sym["price_scale"]
        for bar in batch.get("bars", []):
            if bar.get("symbol") != SYMBOL:
                continue
            close = int(bar["c"]) / scale
            self.history.append(close)
            self.mean.add(close)
        if state.warming_up:
            return []
        self.seen += 1
        held = state.positions.get(SYMBOL)
        if held is None:
            return self.enter(state)
        return self.leave(state, held)

    def leave(self, state, held):
        self.was_holding = True
        if self.entry_bar < 0:
            self.entry_bar = state.seq
        close = self.history[-1]
        mean = self.mean.value
        gap = (close - mean) / mean * 10000.0
        if gap >= RECOVER_BPS:
            return self.sell_all(state, held)
        if held.cost:
            worth = state.value(SYMBOL, held.qty)
            if worth * 100 <= held.cost * (100 - STOP_PCT):
                return self.sell_all(state, held)
        if self.entry_bar + MAX_HOLD <= state.seq:
            return self.sell_all(state, held)
        return []

    def sell_all(self, state, held):
        self.entry_bar = -1
        return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                 "max_slippage_bps": SLIPPAGE}]

    def enter(self, state):
        if self.was_holding:
            self.was_holding = False
            self.exit_bar = state.seq
        self.entry_bar = -1
        if self.exit_bar >= 0 and state.seq - self.exit_bar < COOLDOWN:
            return []
        mean = self.mean.value
        if mean is None or not self.history:
            return []
        close = self.history[-1]
        gap = (close - mean) / mean * 10000.0
        if gap > -DIP_BPS:
            return []
        if self.seen + NO_ENTRY > WINDOW:
            return []
        floor = int(state.hello["fees"]["min_notional_quote_units"])
        if state.cash < floor:
            return []
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": state.cash,
                 "max_slippage_bps": SLIPPAGE}]


_TRADER = Trader()


def on_batch(state, batch):
    return _TRADER.decide(state, batch)


if __name__ == "__main__":
    makefun_bars.run(on_batch)
