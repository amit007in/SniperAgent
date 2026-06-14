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


def geometry_ok(sig: "Signal", feats, params: dict) -> bool:
    """Reject degenerate stop geometry (correctness gate).

    Two failure modes seen in backtest (~27% of trades, ~22% of loss):
      1. Wrong-side stop — stop on/through entry (e.g. gap-up breakout where the
         bar low sits above the broken level). A long with stop >= entry is
         impossible; a short with stop <= entry likewise.
      2. Near-zero risk  — stop within a hair of entry → instant stop-out and a
         meaningless (exploding) R. Require a minimum risk distance: the larger
         of `min_stop_distance_pct` (default 0.5%) and 0.3x ATR%.
    """
    e, s = sig.entry, sig.stop_loss
    if e <= 0 or s <= 0:
        return False
    if sig.direction == "BUY" and s >= e:
        return False
    if sig.direction == "SELL" and s <= e:
        return False
    risk_pct = abs(e - s) / e * 100.0
    last_close = getattr(feats, "last_close", 0.0) or 0.0
    atr_pct = (getattr(feats, "atr_1d", 0.0) / last_close * 100.0) if last_close else 0.0
    floor = max(params.get("min_stop_distance_pct", 0.5), 0.3 * atr_pct)
    return risk_pct >= floor


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
