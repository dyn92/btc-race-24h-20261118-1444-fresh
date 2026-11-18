# Offline replay simulator for bars-replay/v1, mirroring the kit harness's
# fill math exactly (sim/bars/engine.ts: BarsPool, barsBuyFill, barsSellFill).
# Drives the bot's on_batch through the kit SDK's BarsState over the dev
# window bars shipped in the kit. Usage:
#   python3 sim.py [DIP_BPS RECOVER_BPS STOP_FRAC COOLDOWN EMA_SPAN MAX_HOLD]
import gzip
import importlib.util
import json
import math
import os
import sys

KIT = os.path.expanduser("~/workspace/makefun-hunter/kits/btc-race-24h-20261118-1444/btc-race-24h-20261118-1444-kit")
BUILD = os.path.expanduser("~/workspace/makefun-hunter/builds/btc-race-24h-20261118-1444-fresh")
sys.path.insert(0, os.path.join(BUILD, "vendor"))
import makefun_bars  # noqa: E402

BPS = 10000
FEE_BPS = 4
SLIP_BPS = 2
QUOTE_SCALE = 6
PRICE_SCALE = 5
QTY_SCALE = 8
CAPITAL = 1_000_000_000
MIN_NOTIONAL = 1_000_000


def gcd(a, b):
    return math.gcd(a, b)


class Pool:
    def __init__(self, yn, yd, kn, kd):
        g = gcd(yn, yd)
        self.yn, self.yd = yn // g, yd // g
        self.kn, self.kd = kn, kd
        self.unit = 10 ** 6

    @staticmethod
    def of(depth, quote_scale=6):
        d = [x * 10 ** quote_scale for x in depth]
        prod = 1
        for x in d:
            prod *= x
        total = 0
        for i in range(len(d)):
            p = 1
            for j, x in enumerate(d):
                if j != i:
                    p *= x
            total += p
        g = gcd(prod, total)
        return Pool(prod // g, total // g, (prod // g) ** 2, (total // g) ** 2)

    def buy(self, spend):
        yn2 = self.yn + spend * self.unit * self.yd
        num = self.yn * yn2 * self.kd
        den = self.yd * self.yd * self.kn
        return num, den, Pool(yn2, self.yd, self.kn, self.kd)

    def sell(self, value):
        t = self.kn * self.yd + value * self.unit * self.yn * self.kd
        num = t * self.yd
        den = self.kd * self.yn * self.yn
        return num, den, Pool(self.kn * self.yn, t, self.kn, self.kd)


def value(qty, price):
    return qty * price * 10 ** QUOTE_SCALE // 10 ** (QTY_SCALE + PRICE_SCALE)


def buy_fill(notional, open_, depth):
    spend = notional * (BPS - FEE_BPS) // BPS
    pool = Pool.of(depth)
    num, den, _ = pool.buy(spend)
    price = open_ * (BPS + SLIP_BPS) * num // (BPS * den)
    qty = spend * 10 ** (QTY_SCALE + PRICE_SCALE) // (price * 10 ** QUOTE_SCALE)
    return price, spend, notional - spend, qty


def sell_fill(qty, open_, depth):
    pool = Pool.of(depth)
    num, den, _ = pool.sell(value(qty, open_))
    slipped = open_ * (BPS - SLIP_BPS) * den // (BPS * num)
    price = slipped if slipped >= 1 else 1
    gross = value(qty, price)
    proceeds = gross * (BPS - FEE_BPS) // BPS
    return price, gross, gross - proceeds, proceeds


def load_bars():
    bars = []
    with gzip.open(os.path.join(KIT, "dataset/bars/CBBTC.ndjson.gz"), "rt") as f:
        for line in f:
            b = json.loads(line)
            b["symbol"] = "CBBTC"
            bars.append(b)
    return bars


def run_sim(bot, bars, fee_mult=1):
    global FEE_BPS
    old_fee = FEE_BPS
    FEE_BPS = 4 * fee_mult
    try:
        return _run(bot, bars)
    finally:
        FEE_BPS = old_fee


def _run(bot, bars):
    hello = {
        "type": "hello",
        "protocol": "agent-fun/bars-replay/v1",
        "seed": "0",
        "symbols": [{"symbol": "CBBTC", "mint": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",
                     "price_scale": PRICE_SCALE, "qty_scale": QTY_SCALE}],
        "capital": {"quote_units": str(CAPITAL), "quote_scale": QUOTE_SCALE},
        "bar_interval": "1m",
        "first_close_ms": bars[0]["t"],
        "caps": {"max_open_positions": 1, "max_orders_per_bar": 16, "max_weight_bps": 10000},
        "fees": {"fee_bps": FEE_BPS, "slippage_bps": SLIP_BPS, "min_notional_quote_units": str(MIN_NOTIONAL)},
        "latency_bars": 1,
    }
    state = makefun_bars.BarsState(hello)
    cash = CAPITAL
    pos_qty = 0
    pos_cost = 0
    eq_mid = None
    pending = []  # orders decided last batch, fill at this bar's open
    trades = 0
    fees_paid = 0
    peak = CAPITAL
    max_dd = 0.0
    trade_log = []

    def account(latest_close):
        eq = cash + (value(pos_qty, latest_close) if pos_qty else 0)
        ps = [{"symbol": "CBBTC", "qty": str(pos_qty), "cost_quote_units": str(pos_cost)}] if pos_qty else []
        return {"cash_quote_units": str(cash), "equity_quote_units": str(eq), "positions": ps}, eq

    for i, bar in enumerate(bars):
        fills = []
        # Resolve pending orders at this bar's open.
        for o in pending:
            open_ = int(bar["o"])
            depth = [int(x) for x in bar["o_depth"]]
            ref_close = int(bars[i - 1]["c"])  # latest close when decided
            if o["side"] == "buy":
                notional = int(o["notional_quote_units"])
                if notional > cash:
                    fills.append({"status": "rejected", "reason": "insufficient_cash"})
                    continue
                if pos_qty:
                    fills.append({"status": "rejected", "reason": "max_open_positions"})
                    continue
                price, spend, fee, qty = buy_fill(notional, open_, depth)
                if price * BPS > ref_close * (BPS + o["max_slippage_bps"]):
                    fills.append({"status": "rejected", "reason": "max_slippage"})
                    continue
                cash -= notional
                pos_qty += qty
                pos_cost += notional
                fees_paid += fee
                trades += 1
                trade_log.append((i, "buy", price / 1e5, notional / 1e6))
            else:
                qty = int(o["qty"])
                if qty > pos_qty:
                    fills.append({"status": "rejected", "reason": "insufficient_position"})
                    continue
                price, gross, fee, proceeds = sell_fill(qty, open_, depth)
                if price * BPS < ref_close * (BPS - o["max_slippage_bps"]):
                    fills.append({"status": "rejected", "reason": "max_slippage"})
                    continue
                cash += proceeds
                fees_paid += fee
                trades += 1
                trade_log.append((i, "sell", price / 1e5, proceeds / 1e6))
                if qty == pos_qty:
                    pos_qty = 0
                    pos_cost = 0
                else:
                    pos_cost -= pos_cost * qty // pos_qty
                    pos_qty -= qty
            fills.append({"status": "filled"})
        pending = []
        acct, eq = account(int(bar["c"]))
        batch = {"seq": i, "t": bar["t"], "bars": [bar], "fills": fills, "account": acct}
        state.apply_batch(batch)
        orders = state.to_orders(bot.on_batch(state, batch))
        # Decision checks that matter for this bot.
        kept = []
        for o in orders:
            if i == len(bars) - 1:
                continue  # after_end
            if o["side"] == "buy" and int(o["notional_quote_units"]) < MIN_NOTIONAL:
                continue
            if o["side"] == "sell":
                v = value(int(o["qty"]), int(bar["c"]))
                if v < MIN_NOTIONAL and int(o["qty"]) != pos_qty:
                    continue
            kept.append(o)
        pending = kept
        if i == 719:
            eq_mid = eq
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    # Flatten at last close against c_depth.
    if pos_qty:
        last = bars[-1]
        price, gross, fee, proceeds = sell_fill(pos_qty, int(last["c"]), [int(x) for x in last["c_depth"]])
        cash += proceeds
        fees_paid += fee
        trades += 1
        trade_log.append((len(bars) - 1, "flatten", price / 1e5, proceeds / 1e6))
        pos_qty = 0
    return {"pnl": (cash - CAPITAL) / 1e6, "trades": trades, "fees": fees_paid / 1e6,
            "max_dd_pct": max_dd * 100, "log": trade_log,
            "eq_mid": None if eq_mid is None else (eq_mid - CAPITAL) / 1e6}


def load_bot():
    spec = importlib.util.spec_from_file_location("bot_main", os.path.join(BUILD, "main.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def fresh_bot(params=None):
    m = load_bot()
    if params:
        for k, v in params.items():
            setattr(m, k, v)
    return m


if __name__ == "__main__":
    bars = load_bars()
    first = int(bars[0]["c"]) / 1e5
    last = int(bars[-1]["c"]) / 1e5
    print(f"dev window: {len(bars)} bars, close {first:.2f} -> {last:.2f} ({(last/first-1)*100:+.2f}%), hold-cash PnL 0, buy&hold {(last/first-1)*1000:+.2f} USDC gross")
    params = {}
    if len(sys.argv) > 1:
        dip, rec, stop, cd, span, hold = [float(x) for x in sys.argv[1:7]]
        params = {"DIP_BPS": dip, "RECOVER_BPS": rec, "STOP_FRAC": stop,
                  "REENTRY_COOLDOWN_BARS": int(cd), "EMA_SPAN": int(span), "MAX_HOLD_BARS": int(hold)}
    bot = fresh_bot(params)
    r = run_sim(bot, bars)
    print(f"params={params or 'defaults'}")
    print(f"PnL {r['pnl']:+.4f} USDC | trades {r['trades']} | fees {r['fees']:.3f} | maxDD {r['max_dd_pct']:.2f}%")
    for t in r["log"]:
        print("  ", t)
    bot2 = fresh_bot(params)
    r2 = run_sim(bot2, bars, fee_mult=2)
    print(f"fee x2: PnL {r2['pnl']:+.4f} USDC | trades {r2['trades']}")
