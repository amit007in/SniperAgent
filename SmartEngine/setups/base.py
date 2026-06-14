"""Shared setup types and helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Signal:
    setup: str
    direction: str  # BUY | SELL
    entry: float
    target: float
    stop_loss: float
    score: float = 0.0
    claims: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def rr(self) -> float:
        risk = abs(self.entry - self.stop_loss)
        reward = abs(self.target - self.entry)
        if risk < 1e-9:
            return 0.0
        return reward / risk


def pct_of(price: float, pct: float) -> float:
    return price * pct / 100.0


def buffer_price(price: float, pct: float, direction: str, side: str) -> float:
    """Apply SETUP_PARAMS buffer: side entry|stop|target."""
    delta = pct_of(price, pct)
    if direction == "BUY":
        if side == "entry":
            return price + delta
        if side == "stop":
            return price - delta
        return price + delta
    if side == "entry":
        return price - delta
    if side == "stop":
        return price + delta
    return price - delta


def bar_range_pct(bar) -> float:
    if bar.close <= 0:
        return 0.0
    return (bar.high - bar.low) / bar.close * 100.0


def close_position_in_range(bar) -> float:
    rng = bar.high - bar.low
    if rng < 1e-9:
        return 50.0
    return (bar.close - bar.low) / rng * 100.0


def wick_ratio(bar, side: str) -> float:
    body_top = max(bar.open, bar.close)
    body_bot = min(bar.open, bar.close)
    rng = bar.high - bar.low
    if rng < 1e-9:
        return 0.0
    if side == "upper":
        return (bar.high - body_top) / rng
    return (body_bot - bar.low) / rng


def vol_ratio(bar, avg_vol: float) -> float:
    if avg_vol < 1e-9:
        return 0.0
    return bar.volume / avg_vol
