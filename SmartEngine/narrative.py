"""Template narratives and active_levels with registry ids."""

from __future__ import annotations

from SmartEngine.regime import alignment_label
from SmartEngine.registry import ClaimRegistry
from SmartEngine.setups.base import Signal
from SmartEngine.state import TimeframeState


def _tf_view(state: TimeframeState, registry: ClaimRegistry) -> str:
    res = state.nearest_resistance(state.last_close)
    sup = state.nearest_support(state.last_close)
    parts = [
        f"{state.layer} {state.trend} (conf {state.trend_confidence:.0%});",
        f"phase {state.phase}; vol {state.vol_character};",
        f"close {state.last_close:.2f}; VP {state.vp_position}; VWAP {state.vwap_position}.",
    ]
    if res:
        cr = registry.find_by_price(state.layer, "resistance", res)
        rid = cr.id if cr else "CR?"
        parts.append(f"Resistance {res:.2f} ({rid}).")
    if sup:
        cr = registry.find_by_price(state.layer, "support", sup)
        rid = cr.id if cr else "CR?"
        parts.append(f"Support {sup:.2f} ({rid}).")
    return " ".join(parts)


def build_narrative(
    symbol: str,
    s1w: TimeframeState,
    s1d: TimeframeState,
    s4h: TimeframeState,
    registry: ClaimRegistry,
    signal: Signal | None,
    rejection: str | None,
) -> dict:
    align = alignment_label(s1w.trend, s1d.trend, s4h.trend)
    syn_parts = [
        f"{symbol} alignment {align}.",
        f"1W {s1w.trend}, 1D {s1d.trend}, 4H {s4h.trend}.",
    ]
    if signal:
        syn_parts.append(
            f"Selected {signal.setup} {signal.direction} @ {signal.entry}, "
            f"target {signal.target}, stop {signal.stop_loss}, R:R {signal.rr:.2f}, "
            f"confidence {signal.score:.2f}."
        )
    elif rejection:
        syn_parts.append(f"No trade: {rejection}")

    return {
        "1w_view": _tf_view(s1w, registry),
        "1d_view": _tf_view(s1d, registry),
        "4h_view": _tf_view(s4h, registry),
        "synthesis": " ".join(syn_parts),
    }


def build_trend_status(s1w, s1d, s4h) -> dict:
    return {
        "1w": s1w.trend,
        "1d": s1d.trend,
        "4h": s4h.trend,
        "alignment": alignment_label(s1w.trend, s1d.trend, s4h.trend),
    }


def _level_entries(registry: ClaimRegistry, layer: str, claim_type: str, limit: int = 5) -> list[dict]:
    out = []
    for c in registry.active_claims(layer, claim_type):
        if c.price is None:
            continue
        out.append({
            "price": round(c.price, 2),
            "date_evidence": c.first_identified or c.last_tested,
            "type": "swing_high" if claim_type == "resistance" else "swing_low",
            "registry_id": c.id,
        })
        if len(out) >= limit:
            break
    return out


def build_active_levels(registry: ClaimRegistry, s1w, s1d, s4h) -> dict:
    return {
        "1w_resistance": _level_entries(registry, "1W", "resistance"),
        "1w_support": _level_entries(registry, "1W", "support"),
        "1d_resistance": _level_entries(registry, "1D", "resistance"),
        "1d_support": _level_entries(registry, "1D", "support"),
        "4h_resistance": _level_entries(registry, "4H", "resistance"),
        "4h_support": _level_entries(registry, "4H", "support"),
    }
