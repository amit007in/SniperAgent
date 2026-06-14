"""Deterministic rule-based synthesis engine for PriceActionAgent."""

from SmartEngine.drift_guard import compare_decisions, write_report
from SmartEngine.engine import run_smart_synthesis
from SmartEngine.labeling import generate_labels_for_symbol, save_labels
from SmartEngine.scorer import make_scorer, RuleScorer, LogisticScorer
from SmartEngine.train import train_logistic

__all__ = [
    "run_smart_synthesis",
    "make_scorer",
    "RuleScorer",
    "LogisticScorer",
    "generate_labels_for_symbol",
    "save_labels",
    "train_logistic",
    "compare_decisions",
    "write_report",
]
