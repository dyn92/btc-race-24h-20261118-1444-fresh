# Smoke test for the bars-replay/v1 wire protocol (agent-fun/bars-replay/v1).
# Spawns `python3 -P -s main.py`, feeds hello + synthetic 1-minute bars, and
# checks: exactly one reply per batch, seq echoed, a buy appears on an
# engineered dip below EMA(180) - 60 bps, a sell follows on the snap back
# (fills are simulated: a buy reply makes the next batch's account carry
# the position), exit code 0, empty stderr.
import json
import subprocess
import sys

BUILD = __import__("os").path.expanduser(
    "~/workspace/makefun-hunter/builds/btc-race-24h-20261118-1444-fresh")

HELLO = {
    "type": "hello", "protocol": "agent-fun/bars-replay/v1", "seed": "0",
    "symbols": [{"symbol": "CBBTC", "mint": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",
                 "price_scale": 5, "qty_scale": 8}],
    "capital": {"quote_units": "1000000000", "quote_scale": 6},
    "bar_interval": "1m", "first_close_ms": 1795000000000,
    "caps": {"max_open_positions": 1, "max_orders_per_bar": 16, "max_weight_bps": 10000},
    "fees": {"fee_bps": 4, "slippage_bps": 2, "min_notional_quote_units": "1000000"},
    "latency_bars": 1,
}

# price_scale 5: 100_000.00 USDC -> c = 10_000_000_000.
BASE = 10_000_000_000


def bar(t, close):
    c = str(close)
    return {"symbol": "CBBTC", "t": t, "o": c, "h": c, "l": c, "c": c,
            "o_depth": ["1000000", "1000000"], "c_depth": ["1000000", "1000000"]}


def main():
    t0 = 1795000000000
    # 200 flat bars at BASE so EMA(180) warms up at BASE.
    closes = [BASE] * 200
    # Engineered dip: -1% (below EMA - 60 bps) for 3 bars, then snap back
    # to +1% above EMA for 3 bars, then flat.
    closes += [int(BASE * 0.99)] * 3
    closes += [int(BASE * 1.01)] * 3
    closes += [BASE] * 10
    # First pass: feed flat account, collect replies to learn when the buy
    # fires; then replay with fills simulated so the sell path is exercised.
    lines = [json.dumps(HELLO)]
    for i, c in enumerate(closes):
        batch = {"type": "batch", "seq": i, "t": t0 + (i + 1) * 60000,
                 "bars": [bar(t0 + (i + 1) * 60000, c)],
                 "fills": [], "account": {"cash_quote_units": "1000000000",
                 "equity_quote_units": "1000000000", "positions": []}}
        lines.append(json.dumps(batch))
    lines.append(json.dumps({"type": "done", "seq": len(closes),
                             "account": {"cash_quote_units": "1000000000",
                             "equity_quote_units": "1000000000", "positions": []}}))
    proc = subprocess.run([sys.executable, "-P", "-s", BUILD + "/main.py"],
                          input="\n".join(lines) + "\n", capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, f"exit {proc.returncode}, stderr: {proc.stderr[:2000]}"
    assert proc.stderr == "", f"stderr not empty: {proc.stderr[:2000]}"
    replies = [json.loads(l) for l in proc.stdout.strip().split("\n")]
    assert len(replies) == len(closes), f"{len(replies)} replies for {len(closes)} batches"
    for i, r in enumerate(replies):
        assert r["type"] == "reply" and r["seq"] == i, f"bad reply {i}: {r}"
        assert len(r["orders"]) <= 16, f"too many orders batch {i}"
    buys = [i for i, r in enumerate(replies)
            if any(o["side"] == "buy" for o in r["orders"])]
    assert buys, "no buy fired on the engineered dip"
    assert replies[buys[0]]["orders"][0]["notional_quote_units"] == "1000000000", replies[buys[0]]
    # Second pass with fills: after the buy batch, the account carries a
    # position (qty chosen so value ~= cost at the dip price), cash 0.
    buy_i = buys[0]
    dip_price = closes[buy_i]
    qty = 1_000_000_000 * 10 ** 13 // (dip_price * 10 ** 6)
    lines = [json.dumps(HELLO)]
    for i, c in enumerate(closes):
        if i > buy_i:
            acct = {"cash_quote_units": "0",
                    "equity_quote_units": "1000000000",
                    "positions": [{"symbol": "CBBTC", "qty": str(qty),
                                   "cost_quote_units": "1000000000"}]}
        else:
            acct = {"cash_quote_units": "1000000000",
                    "equity_quote_units": "1000000000", "positions": []}
        batch = {"type": "batch", "seq": i, "t": t0 + (i + 1) * 60000,
                 "bars": [bar(t0 + (i + 1) * 60000, c)],
                 "fills": [], "account": acct}
        lines.append(json.dumps(batch))
    lines.append(json.dumps({"type": "done", "seq": len(closes),
                             "account": {"cash_quote_units": "0",
                             "equity_quote_units": "1000000000",
                             "positions": [{"symbol": "CBBTC", "qty": str(qty),
                                            "cost_quote_units": "1000000000"}]}}))
    proc = subprocess.run([sys.executable, "-P", "-s", BUILD + "/main.py"],
                          input="\n".join(lines) + "\n", capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, f"pass2 exit {proc.returncode}, stderr: {proc.stderr[:2000]}"
    assert proc.stderr == "", f"pass2 stderr not empty: {proc.stderr[:2000]}"
    replies2 = [json.loads(l) for l in proc.stdout.strip().split("\n")]
    assert len(replies2) == len(closes)
    sells = [i for i, r in enumerate(replies2)
             if any(o["side"] == "sell" for o in r["orders"])]
    assert sells, "no sell fired on the snap back"
    assert sells[0] > buy_i, "sell before buy"
    print(f"SMOKE OK: {len(replies2)} replies, 1/batch, seq echoed, "
          f"buy at batch {buy_i}, sell at batch {sells[0]}, "
          f"exit 0, stderr empty")


if __name__ == "__main__":
    main()
