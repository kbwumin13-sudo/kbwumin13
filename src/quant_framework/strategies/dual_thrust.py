"""Dual Thrust breakout strategy adapted for daily A-share data."""

from __future__ import annotations

import backtrader as bt


class DualThrustStrategy(bt.Strategy):
    """Trade breakouts above/below a volatility range around the current open."""

    params = (
        ("window_size", 5),
        ("k1", 0.2),
        ("k2", 0.5),
        ("position_size", 0.95),
    )

    def __init__(self) -> None:
        if self.p.window_size <= 0:
            raise ValueError("window_size must be positive.")
        if self.p.k1 <= 0 or self.p.k2 <= 0:
            raise ValueError("k1 and k2 must be positive.")
        if not 0 < self.p.position_size <= 1:
            raise ValueError("position_size must be in (0, 1].")
        self.order = None

    def notify_order(self, order) -> None:  # noqa: ANN001 - backtrader dynamic object.
        if order.status in [order.Completed, order.Canceled, order.Margin, order.Rejected]:
            self.order = None

    def next(self) -> None:
        if self.order or len(self.data) <= self.p.window_size:
            return

        hh = max(self.data.high.get(ago=-1, size=self.p.window_size))
        hc = max(self.data.close.get(ago=-1, size=self.p.window_size))
        lc = min(self.data.close.get(ago=-1, size=self.p.window_size))
        ll = min(self.data.low.get(ago=-1, size=self.p.window_size))
        price_range = max(hh - lc, hc - ll)

        upper_bound = self.data.open[0] + self.p.k1 * price_range
        lower_bound = self.data.open[0] - self.p.k2 * price_range

        if not self.position and self.data.close[0] > upper_bound:
            size = int(self.broker.getcash() * self.p.position_size / self.data.close[0])
            if size > 0:
                self.order = self.buy(size=size)
        elif self.position and self.data.close[0] < lower_bound:
            self.order = self.close()
