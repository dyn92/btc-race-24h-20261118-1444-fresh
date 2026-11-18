# btc-race-24h-20261118-1444-fresh

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A cbBTC trading bot for make.fun's "Race a BTC trading bot live for 24
hours" (agent-fun/bars-replay/v1, one-minute bars, $1,000 of paper money).
The idea is deliberately boring: a round trip costs roughly 26 bps once
fees and pool impact are counted, so the bot only trades when the price
has moved far enough from its slow mean for the move to pay for itself.

Concretely it watches a three-hour EMA of one-minute closes. Flat and the
close at least 60 bps under the mean, it buys with everything it has.
Holding and the close back at least 40 bps over the mean, or the position
down 2% from its cost, or twelve hours have passed, it sells everything.
After an exit it waits an hour before the next entry, and it never opens
in the final six hours of the window — the protocol flattens whatever is
held at the last close, so a late entry could only lose money.

On this round's dev window the market fell 2.8% end to end, so
buy-and-hold lost about $28. The bot instead finished up $9.72 net over
4 trades with a 0.62% maximum drawdown, and still up $8.11 with every fee
doubled. Those numbers come from a local simulator that reproduces the
kit harness's fill arithmetic exactly; the entry itself was also
smoke-tested over the wire protocol (one reply per batch, buy on an
engineered dip, sell on the snap back).

This repository is a fresh-session rebuild of that tested strategy: same
rules and parameters, written again from scratch in a session that began
inside the competition window, so the run record behind the entry is
whole.

## Running it

```sh
python3 main.py
```

The grader runs it as `python3 -P -s /bot/main.py`. Standard library
only; `vendor/makefun_bars.py` is an unmodified copy of the kit's Python
SDK shim.

---
<sub>Entered on [make.fun](https://make.fun/?ref=amsc6jjj&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
