"""Dip-bounce bot for btc-race-24h-20261117-0603 (agent-fun/bars-replay/v1).

Idea: this market mean-reverts on the hourly scale — sharp one-hour drops
bounce back toward the two-hour mean. So the bot buys a sharp dip and takes
a fixed profit, cutting the trade if the dip keeps falling.

  * Entry (flat only): the close is at least 1.00% below the close 60 bars
    ago AND at or below the 120-bar simple moving average of closes.
    Buys with all cash.
  * Exit (long only): sell everything when the close is 2.00% above the
    buy's fill price (take profit), or 1.50% below it (stop loss). If
    neither fires within 240 bars, take the trade off once it is 0.50%
    above the fill price, rather than carry a stale position all day.
  * One position at a time, full size, at most a handful of trades a day.

Everything is integer math on the bar closes the harness sends; no clock,
no network, no randomness, no files. The fill price of the entry buy (from
the next batch's fills) is the reference for the exit thresholds.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# Lookback for the dip measurement (bars) and its trigger, in basis points.
DIP_BARS = 60
DIP_BPS = 100
# Mean filter: only buy at or below the N-bar average close.
MEAN_BARS = 120
# Exit thresholds from the entry fill price, in basis points.
TAKE_BPS = 200
STOP_BPS = 150
# Stale-trade exit: after this many bars, bank a smaller gain instead of
# waiting for the full take-profit level.
STALE_BARS = 240
STALE_BPS = 50
# Max slippage vs the latest close the bot accepts on a fill.
MAX_SLIP_BPS = 200

closes = []          # close of every bar seen, in price units (int)
mean_sum = 0         # running sum of the last MEAN_BARS closes
entry_price = None   # fill price of the open buy, or None while flat
entry_seq = None     # batch seq at which the open buy filled


def on_batch(state, batch):
    global mean_sum, entry_price, entry_seq

    # Learn the entry fill price from this batch's fills.
    for fill in batch.get("fills", []):
        if fill.get("symbol") == SYMBOL and fill.get("status") == "filled":
            if fill.get("side") == "buy":
                entry_price = int(fill["price"])
                entry_seq = state.seq
            elif fill.get("side") == "sell":
                entry_price = None
                entry_seq = None

    bar = None
    for b in batch.get("bars", []):
        if b.get("symbol") == SYMBOL:
            bar = b
            break
    if bar is None or state.warming_up:
        return []

    close = int(bar["c"])
    closes.append(close)
    mean_sum += close
    if len(closes) > MEAN_BARS:
        mean_sum -= closes[-MEAN_BARS - 1]

    holding = state.positions.get(SYMBOL)
    if holding is not None and holding.qty > 0:
        if entry_price is None:
            # Position open but fill not seen yet (should not happen); hold.
            return []
        move_bps = (close - entry_price) * 10000 // entry_price
        stale = (entry_seq is not None and state.seq - entry_seq >= STALE_BARS
                 and move_bps >= STALE_BPS)
        if move_bps >= TAKE_BPS or move_bps <= -STOP_BPS or stale:
            return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                     "max_slippage_bps": MAX_SLIP_BPS}]
        return []

    # Flat: look for a dip.
    if len(closes) <= MEAN_BARS:
        return []
    dip_bps = (close - closes[-DIP_BARS - 1]) * 10000 // closes[-DIP_BARS - 1]
    mean = mean_sum // MEAN_BARS
    if dip_bps <= -DIP_BPS and close <= mean:
        amount = state.cash
        room = state.weight_room(SYMBOL)
        if room < amount:
            amount = room
        if amount < int(state.hello["fees"]["min_notional_quote_units"]):
            return []
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": amount,
                 "max_slippage_bps": MAX_SLIP_BPS}]
    return []


makefun_bars.run(on_batch)
