"""Built-in strategy implementations."""

from quant_framework.strategies.boll_breakout import BollingerBreakoutStrategy
from quant_framework.strategies.dual_thrust import DualThrustStrategy
from quant_framework.strategies.ma_cross import MovingAverageCrossStrategy
from quant_framework.strategies.turtle import TurtleTradingStrategy

STRATEGIES = {
    "ma_cross": MovingAverageCrossStrategy,
    "dual_thrust": DualThrustStrategy,
    "boll_breakout": BollingerBreakoutStrategy,
    "turtle": TurtleTradingStrategy,
}

__all__ = [
    "BollingerBreakoutStrategy",
    "DualThrustStrategy",
    "MovingAverageCrossStrategy",
    "STRATEGIES",
    "TurtleTradingStrategy",
]
