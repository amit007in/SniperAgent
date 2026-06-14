"""Schema parity tests for SmartEngine output."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_PA = _REPO / "PriceActionAgent"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_PA))

from SmartEngine.serialize import serialize_no_trade  # noqa: E402
from SmartEngine.scorer import RuleScorer, make_scorer  # noqa: E402
from synthesis import _is_complete_synthesis  # noqa: E402


def test_no_trade_schema_complete():
    out = serialize_no_trade("RELIANCE", "Test rejection", "2025-06-12")
    assert _is_complete_synthesis(out)
    td = out["trade_decision"]
    assert td["action"] == "NO_TRADE"
    assert "full_narrative" in out
    assert "claim_registry" in out
    assert "trend_status" in out
    assert "active_levels" in out
    assert "1d_resistance" in out["active_levels"]
    assert "data_integrity_check" in out


def test_trade_decision_keys():
    out = serialize_no_trade("HDFCBANK", "No setup", "2025-06-12")
    td = out["trade_decision"]
    for key in ("action", "setup", "entry", "target", "stop_loss", "rejection", "next_plan"):
        assert key in td


def test_alignment_labels():
    from SmartEngine.regime import alignment_label, TREND_UP, TREND_DOWN
    assert alignment_label(TREND_UP, TREND_UP, TREND_UP) == "ALIGNED_BULLISH"
    assert alignment_label(TREND_DOWN, TREND_DOWN, TREND_DOWN) == "ALIGNED_BEARISH"


def test_make_scorer_rule():
    scorer = make_scorer({"scorer": "rule"})
    assert isinstance(scorer, RuleScorer)

