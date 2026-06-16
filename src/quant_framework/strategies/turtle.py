"""Turtle trading strategy adapted for daily A-share data."""

from __future__ import annotations

import backtrader as bt


class TurtleTradingStrategy(bt.Strategy):
    """Donchian breakout with ATR-based pyramiding and stop loss."""

    params = (
        ("entry_window", 20),
        ("exit_window", 10),
        ("atr_window", 20),
        ("risk_fraction", 0.01),
        ("max_units", 4),
        ("add_atr_multiple", 0.5),
        ("stop_atr_multiple", 2.0),
    )

    def __init__(self) -> None:
        if self.p.entry_window <= 1 or self.p.exit_window <= 1 or self.p.atr_window <= 1:
            raise ValueError("entry_window, exit_window and atr_window must be greater than 1.")
        if not 0 < self.p.risk_fraction <= 1:
            raise ValueError("risk_fraction must be in (0, 1].")
        if self.p.max_units <= 0:
            raise ValueError("max_units must be positive.")

        self.atr = bt.indicators.AverageTrueRange(self.data, period=self.p.atr_window)
        self.entry_high = bt.indicators.Highest(self.data.high(-1), period=self.p.entry_window)
        self.exit_low = bt.indicators.Lowest(self.data.low(-1), period=self.p.exit_window)
        self.order = None
        self.units = 0
        self.last_buy_price = None

    def notify_order(self, order) -> None:  # noqa: ANN001 - backtrader dynamic object.
        if order.status == order.Completed and order.isbuy():
            self.units += 1
            self.last_buy_price = float(order.executed.price)
        elif order.status == order.Completed and order.issell() and not self.position:
            self.units = 0
            self.last_buy_price = None

        if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
            self.order = None

    def next(self) -> None:
        if self.order or len(self.data) <= max(self.p.entry_window, self.p.exit_window, self.p.atr_window):
            return

        price = float(self.data.close[0])
        atr = float(self.atr[0])
        if atr <= 0:
            return

        if not self.position:
            if price > self.entry_high[0]:
                size = self._unit_size(atr)
                if size > 0:
                    self.order = self.buy(size=size)
            return

        if self.last_buy_price is None:
            self.last_buy_price = price

        stop_price = self.last_buy_price - self.p.stop_atr_multiple * atr
        add_price = self.last_buy_price + self.p.add_atr_multiple * atr

        if price < self.exit_low[0] or price <= stop_price:
            self.order = self.close()
        elif self.units < self.p.max_units and price >= add_price:
            size = self._unit_size(atr)
            if size > 0:
                self.order = self.buy(size=size)

    def _unit_size(self, atr: float) -> int:
        risk_cash = self.broker.getvalue() * self.p.risk_fraction
        by_risk = int(risk_cash / atr)
        by_cash = int(self.broker.getcash() / self.data.close[0])
        return max(0, min(by_risk, by_cash))
