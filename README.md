# btc-race-24h-20261118-1444-fresh

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

Slow mean-reversion cbBTC trading bot for the make.fun "Race a BTC trading
bot live for 24 hours" competition (agent-fun/bars-replay/v1, 1-minute bars,
$1,000 paper). It buys only genuine dislocations — the close at or below
EMA(180) − 60 bps — and sells the snap back to EMA(180) + 40 bps, with a −2%
stop and a 720-bar max hold. One position at a time, all-in/all-out, so the
trade count stays in single digits and the ~26 bps round-trip cost stays a
small share of the moves harvested. It opens no new position in the last
360 bars of the window: whatever is held at the end is flattened at the last
close, so a late entry has no time to recover.

This round's dev window fell 2.8% end to end (buy-and-hold: −28 USDC gross).
On that window the strategy makes +9.72 USDC net (4 trades, max drawdown
0.62%), and +8.11 USDC with every fee doubled, graded locally with a
simulator that replicates the kit harness's fill math exactly (the kit's
grade-local driver runs Node bots only in this environment, so the Python
entry was graded against the replica and smoke-tested over the wire
protocol).

This is a fresh-session rebuild of the same tested strategy, built in a
session that started inside the competition window so its run record is
whole.

## Run it

```sh
python3 main.py
```

Run by the grader as `python3 -P -s /bot/main.py`; standard library only
(`vendor/makefun_bars.py` is a byte-identical copy of the kit SDK shim).

---
<sub>Entered on [make.fun](https://make.fun/?ref=amsc6jjj&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
