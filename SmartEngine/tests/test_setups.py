"""Golden-case setup unit tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from SmartEngine.setups.base import Signal, vol_ratio, wick_ratio
from SmartEngine.setups.structure import scan_b1_breakout, scan_s1_support_bounce
from SmartEngine.state import TimeframeState
from SmartEngine.registry import ClaimRegistry
from SmartEngine.features import FeatureFrame


class Bar:
    def __init__(self, date, o, h, l, c, v=1_000_000):
        self.date = date
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v


def _make_uptrend_daily(n=30, base=100.0):
    bars = []
    for i in range(n):
        p = base + i * 0.5
        bars.append(Bar(f"2025-01-{i+1:02d}", p, p + 1, p - 0.5, p + 0.3, 1_200_000))
    # breakout bar
    last = bars[-1]
    bars[-1] = Bar(last.date, last.close, last.close + 3, last.close - 0.2, last.close + 2.5, 3_000_000)
    return bars


def test_b1_breakout_detected():
    daily = _make_uptrend_daily()
    s1w = TimeframeState("1W", "UPTREND", daily[-1].close)
    s1d = TimeframeState("1D", "UPTREND", daily[-1].close, period_high=max(b.high for b in daily))
    feats = FeatureFrame("", daily[-1].close, 100, 95, 1_000_000, 1, 1, 0, 1, 0.5)
    params = {
        "breakout_lookback_bars": 5,
        "breakout_vol_min": 1.5,
        "entry_buffer_pct": 0.25,
        "min_rr_ratio": 1.0,
    }
    sig = scan_b1_breakout(s1w, s1d, daily, feats, params, {"BUY"})
    assert sig is not None
    assert sig.setup == "B1"
    assert sig.direction == "BUY"
    assert sig.rr >= 1.0


def test_b1_excluded_when_weekly_transitioning():
    """Structure setups must self-exclude under a TRANSITIONING weekly."""
    daily = _make_uptrend_daily()
    s1w = TimeframeState("1W", "TRANSITIONING", daily[-1].close)
    s1d = TimeframeState("1D", "UPTREND", daily[-1].close, period_high=max(b.high for b in daily))
    feats = FeatureFrame("", daily[-1].close, 100, 95, 1_000_000, 1, 1, 0, 1, 0.5)
    params = {
        "breakout_lookback_bars": 5,
        "breakout_vol_min": 1.5,
        "entry_buffer_pct": 0.25,
        "min_rr_ratio": 1.0,
    }
    assert scan_b1_breakout(s1w, s1d, daily, feats, params, {"BUY"}) is None


def test_wick_ratio_upper():
    bar = Bar("d", 100, 110, 95, 102)
    assert wick_ratio(bar, "upper") > 0.5


def test_vol_ratio():
    bar = Bar("d", 100, 105, 99, 104, 2_000_000)
    assert vol_ratio(bar, 1_000_000) == 2.0
