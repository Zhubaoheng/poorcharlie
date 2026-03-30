"""Backtrader Strategy — replays pre-computed decisions."""

from __future__ import annotations

import logging
from datetime import date, datetime

import backtrader as bt

logger = logging.getLogger(__name__)

# Transaction cost parameters (per spec §4.2)
COMMISSION_RATE = 0.00025   # 0.025% per side
STAMP_TAX_RATE = 0.0005     # 0.05% sell only
SLIPPAGE_RATE = 0.001       # 0.1% per side


class MungerStrategy(bt.Strategy):
    """Munger-style concentrated investment strategy.

    Reads pre-computed decisions from a dict and executes them.
    Monitors price triggers between scan dates.
    """

    params = dict(
        decisions={},           # {date_str: {ticker: target_weight, ...}}
        price_trigger_down=0.20,  # -20% triggers re-evaluation
        price_trigger_up=0.50,    # +50% triggers re-evaluation
        price_decisions={},     # {date_str: {ticker: target_weight, ...}} from price triggers
    )

    def __init__(self):
        self.entry_prices = {}   # ticker -> price at last evaluation
        self.order_pending = {}  # ticker -> order object

    def log(self, txt: str) -> None:
        dt = self.datetime.date()
        logger.info("[%s] %s", dt, txt)

    def next(self):
        today = self.datetime.date()
        today_str = today.isoformat()

        # Check for scheduled scan decisions
        if today_str in self.p.decisions:
            self._execute_rebalance(self.p.decisions[today_str])
            return

        # Check for price-triggered decisions
        if today_str in self.p.price_decisions:
            self._execute_rebalance(self.p.price_decisions[today_str])
            return

    def _execute_rebalance(self, target: dict[str, float]) -> None:
        """Rebalance portfolio to match target weights."""
        portfolio_value = self.broker.getvalue()

        # Sell positions not in target
        for data in self.datas:
            ticker = data._name
            pos = self.getposition(data)
            if pos.size > 0 and ticker not in target:
                self.log(f"SELL ALL {ticker} (not in target)")
                self.close(data)

        # Adjust existing positions and open new ones
        for ticker, weight in target.items():
            data = self._get_data_by_name(ticker)
            if data is None:
                self.log(f"WARNING: no data feed for {ticker}")
                continue

            target_value = portfolio_value * weight
            current_pos = self.getposition(data)
            current_value = current_pos.size * data.close[0] if current_pos.size > 0 else 0
            diff = target_value - current_value

            if abs(diff) < portfolio_value * 0.01:
                continue  # skip tiny adjustments

            if diff > 0:
                size = int(diff / data.close[0] / 100) * 100  # round to lot size
                if size > 0:
                    self.log(f"BUY {ticker} size={size} target_weight={weight:.1%}")
                    self.buy(data, size=size)
            elif diff < 0:
                size = int(abs(diff) / data.close[0] / 100) * 100
                if size > 0:
                    self.log(f"SELL {ticker} size={size} target_weight={weight:.1%}")
                    self.sell(data, size=size)

            self.entry_prices[ticker] = data.close[0]

    def _get_data_by_name(self, name: str):
        for data in self.datas:
            if data._name == name:
                return data
        return None

    def notify_trade(self, trade):
        if trade.isclosed:
            self.log(
                f"TRADE CLOSED {trade.data._name}: "
                f"PnL={trade.pnl:.2f} Net={trade.pnlcomm:.2f}"
            )


class BacktestCommission(bt.CommInfoBase):
    """A-share commission: buy commission + sell commission + stamp tax."""

    params = dict(
        commission=COMMISSION_RATE,
        stamp_tax=STAMP_TAX_RATE,
        slippage=SLIPPAGE_RATE,
    )

    def _getcommission(self, size, price, pseudoexec):
        commission = abs(size) * price * self.p.commission
        slippage = abs(size) * price * self.p.slippage
        if size < 0:  # selling
            commission += abs(size) * price * self.p.stamp_tax
        return commission + slippage
