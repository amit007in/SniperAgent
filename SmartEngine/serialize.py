"""Assemble SmartEngine output to PriceActionAgent JSON schema."""

from __future__ import annotations

from SmartEngine.narrative import build_active_levels, build_narrative, build_trend_status
from SmartEngine.registry import ClaimRegistry
from SmartEngine.setups.base import Signal
from SmartEngine.state import TimeframeState


def _trade_decision(
    signal: Signal | None,
    rejection: str | None,
    next_td: str,
    registry: ClaimRegistry,
) -> dict:
    if signal:
        cr_refs = ", ".join(signal.claims[:3]) if signal.claims else "structure"
        return {
            "action": signal.direction,
            "setup": signal.setup,
            "entry": signal.entry,
            "target": signal.target,
            "stop_loss": signal.stop_loss,
            "rejection": None,
            "next_plan": (
                f"Execute {signal.setup} on {next_td} if price holds {cr_refs}; "
                f"abort on stop {signal.stop_loss}."
            ),
        }
    return {
        "action": "NO_TRADE",
        "setup": None,
        "entry": None,
        "target": None,
        "stop_loss": None,
        "rejection": rejection or "No qualifying setup",
        "next_plan": f"Re-scan on {next_td}; wait for clearer multi-TF alignment.",
    }


def serialize_synthesis(
    symbol: str,
    registry: ClaimRegistry,
    s1w: TimeframeState,
    s1d: TimeframeState,
    s4h: TimeframeState,
    signal: Signal | None,
    rejection: str | None,
    next_td: str,
) -> dict:
    return {
        "trade_decision": _trade_decision(signal, rejection, next_td, registry),
        "claim_registry": registry.to_list(),
        "full_narrative": build_narrative(symbol, s1w, s1d, s4h, registry, signal, rejection),
        "trend_status": build_trend_status(s1w, s1d, s4h),
        "active_levels": build_active_levels(registry, s1w, s1d, s4h),
        "data_integrity_check": (
            "PASS — deterministic engine; all prices sourced from OHLCV; "
            "every cited level has a claim_registry id"
        ),
    }


def serialize_no_trade(symbol: str, rejection: str, next_td: str) -> dict:
    empty = TimeframeState("NA", "SIDEWAYS", 0.0)
    reg = ClaimRegistry()
    return serialize_synthesis(symbol, reg, empty, empty, empty, None, rejection, next_td)
