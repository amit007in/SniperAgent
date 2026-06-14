"""Assert no look-ahead in labeling replay."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_PA = _REPO / "PriceActionAgent"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_PA))

from SmartEngine.labeling import label_signal
from SmartEngine.setups.base import Signal


class Bar:
    def __init__(self, date, o, h, l, c):
        self.date = date
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = 1_000_000


def test_label_uses_only_forward_bars():
    sig = Signal("B1", "BUY", 100.0, 110.0, 95.0, score=0.7)
    forward = [
        Bar("2025-02-01", 100, 105, 99, 104),
        Bar("2025-02-02", 104, 111, 103, 110),
    ]
    lab = label_signal("TEST", "2025-01-31", sig, forward, timeout_bars=5)
    assert lab.outcome == "win"
    assert lab.bars_held == 2


def test_registry_chronological_no_future_pivots():
    from SmartEngine.registry import ClaimRegistry
    from SmartEngine.features import FeatureFrame

    class C:
        def __init__(self, d, h, l, c):
            self.date = d
            self.high = h
            self.low = l
            self.close = c
            self.open = c
            self.volume = 1e6

    candles = [C(f"2025-01-{i:02d}", 100 + i, 99 + i, 100 + i) for i in range(1, 15)]
    feats = FeatureFrame("", candles[-1].close, 0, 0, 1e6, 0, 0, 0, 0, 0)
    reg = ClaimRegistry.build_chronological(candles, [], [], feats, {"sr_zone_tolerance_pct": 0.5})
    for c in reg.claims:
        assert c.first_identified <= str(candles[-1].date)
