"""Classic moving-average crossover strategy for backtrader."""

from __future__ import annotations

import backtrader as bt


class MovingAverageCrossStrategy(bt.Strategy):
    """Buy when the short SMA crosses above the long SMA, sell on cross down."""

    params = (
        ("short_window", 20),
        ("long_window", 60),
        ("position_size", 0.95),
    )

    def __init__(self) -> None:
        if self.p.short_window <= 0 or self.p.long_window <= 0:
            raise ValueError("Moving average windows must be positive.")
        if self.p.short_window >= self.p.long_window:
            raise ValueError("short_window must be smaller than long_window.")
        if not 0 < self.p.position_size <= 1:
            raise ValueError("position_size must be in (0, 1].")

        short_ma = bt.indicators.SimpleMovingAverage(self.data.close, period=self.p.short_window)
        long_ma = bt.indicators.SimpleMovingAverage(self.data.close, period=self.p.long_window)
        self.crossover = bt.indicators.CrossOver(short_ma, long_ma)
        self.order = None

    def notify_order(self, order) -> None:  # noqa: ANN001 - backtrader uses dynamic order objects.
        if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
            self.order = None

    def next(self) -> None:
        if self.order:
            return

        if not self.position and self.crossover > 0:
            cash_to_use = self.broker.getcash() * self.p.position_size
            size = int(cash_to_use / self.data.close[0])
            if size > 0:
                self.order = self.buy(size=size)
        elif self.position and self.crossover < 0:
            self.order = self.close()
