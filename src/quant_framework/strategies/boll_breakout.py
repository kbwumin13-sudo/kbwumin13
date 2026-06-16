"""Bollinger Band breakout strategy adapted for backtrader."""

from __future__ import annotations

import backtrader as bt


class BollingerBreakoutStrategy(bt.Strategy):
    """Buy upper-band breakouts and exit lower-band breakdowns."""

    params = (
        ("period", 20),
        ("devfactor", 2.0),
        ("position_size", 0.95),
    )

    def __init__(self) -> None:
        if self.p.period <= 1:
            raise ValueError("period must be greater than 1.")
        if self.p.devfactor <= 0:
            raise ValueError("devfactor must be positive.")
        if not 0 < self.p.position_size <= 1:
            raise ValueError("position_size must be in (0, 1].")

        self.boll = bt.indicators.BollingerBands(
            self.data.close,
            period=self.p.period,
            devfactor=self.p.devfactor,
        )
        self.order = None

    def notify_order(self, order) -> None:  # noqa: ANN001 - backtrader dynamic object.
        if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
            self.order = None

    def next(self) -> None:
        if self.order:
            return

        if not self.position and self.data.close[0] > self.boll.top[0]:
            size = int(self.broker.getcash() * self.p.position_size / self.data.close[0])
            if size > 0:
                self.order = self.buy(size=size)
        elif self.position and self.data.close[0] < self.boll.bot[0]:
            self.order = self.close()
