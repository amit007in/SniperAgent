"""Drift guard and training CLI tests."""

from __future__ import annotations

from SmartEngine.drift_guard import compare_decisions, DriftReport


def test_drift_agreement():
    engine = {
        "A": {"trade_decision": {"action": "BUY", "setup": "B1"}},
        "B": {"trade_decision": {"action": "NO_TRADE", "setup": None}},
    }
    llm = {
        "A": {"trade_decision": {"action": "BUY", "setup": "B1"}},
        "B": {"trade_decision": {"action": "NO_TRADE", "setup": None}},
    }
    r = compare_decisions(engine, llm)
    assert r.action_agreement == 1.0
    assert not r.tolerance_exceeded


def test_drift_disagreement():
    engine = {"A": {"trade_decision": {"action": "BUY", "setup": "B1"}}}
    llm = {"A": {"trade_decision": {"action": "SELL", "setup": "S1"}}}
    r = compare_decisions(engine, llm, action_tolerance=0.99)
    assert r.tolerance_exceeded
    assert len(r.disagreements) == 1
