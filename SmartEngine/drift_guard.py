"""Drift guard — compare SmartEngine vs LLM decision streams."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class DriftReport:
    total: int
    action_agreement: float
    setup_agreement: float
    disagreements: list[dict]
    tolerance_exceeded: bool


def _normalize(td: dict) -> tuple[str, str]:
    action = (td or {}).get("action", "NO_TRADE")
    setup = (td or {}).get("setup") or "NO_TRADE"
    return action, setup


def compare_decisions(
    engine_results: dict[str, dict],
    llm_results: dict[str, dict],
    *,
    action_tolerance: float = 0.85,
    setup_tolerance: float = 0.70,
) -> DriftReport:
    """
    Compare per-symbol synthesis dicts.

    engine_results / llm_results: {symbol: synthesis_dict}
    """
    common = set(engine_results) & set(llm_results)
    if not common:
        return DriftReport(0, 1.0, 1.0, [], False)

    action_match = setup_match = 0
    disagreements = []

    for sym in sorted(common):
        ea, es = _normalize(engine_results[sym].get("trade_decision"))
        la, ls = _normalize(llm_results[sym].get("trade_decision"))
        if ea == la:
            action_match += 1
        if es == ls or (ea == "NO_TRADE" and la == "NO_TRADE"):
            setup_match += 1
        if ea != la or (es != ls and ea != "NO_TRADE" and la != "NO_TRADE"):
            disagreements.append({
                "symbol": sym,
                "engine": {"action": ea, "setup": es},
                "llm": {"action": la, "setup": ls},
            })

    n = len(common)
    action_agree = action_match / n
    setup_agree = setup_match / n
    exceeded = action_agree < action_tolerance or setup_agree < setup_tolerance

    return DriftReport(
        total=n,
        action_agreement=round(action_agree, 3),
        setup_agreement=round(setup_agree, 3),
        disagreements=disagreements,
        tolerance_exceeded=exceeded,
    )


def load_decisions_from_db(store, symbols: list[str], decision_date: str) -> dict[str, dict]:
    """Load raw_json trade decisions from PAStore if available."""
    out = {}
    for sym in symbols:
        row = store.get_latest_decision(sym, decision_date) if hasattr(store, "get_latest_decision") else None
        if row and row.get("raw_json"):
            raw = row["raw_json"]
            out[sym] = json.loads(raw) if isinstance(raw, str) else raw
    return out


def write_report(report: DriftReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2))
