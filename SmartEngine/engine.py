"""SmartEngine entry point — drop-in replacement for LLM synthesis."""

from __future__ import annotations

import logging

from SmartEngine.features import build_features
from SmartEngine.registry import ClaimRegistry
from SmartEngine.reconcile import (
    check_hard_disqualifiers,
    filter_signals_by_direction,
    permitted_directions,
)
from SmartEngine.scorer import make_scorer, select_best
from SmartEngine.serialize import serialize_no_trade, serialize_synthesis
from SmartEngine.setups.base import geometry_ok
from SmartEngine.setups.momentum import scan_all_momentum
from SmartEngine.setups.structure import scan_all_structure
from SmartEngine.setups.trend import scan_all_trend
from SmartEngine.state import extract_state

log = logging.getLogger(__name__)

# Hunt protocol priority (prompts.py)
HUNT_ORDER_BUY = ["B1", "B2", "B3", "B4", "T1", "T2", "M1", "M2"]
HUNT_ORDER_SELL = ["S1", "S2", "S3", "S4", "T1", "T2", "M1", "M2"]


def _load_config():
    try:
        from config import SETUP_PARAMS, SMART_PARAMS
    except ImportError:
        import sys
        from pathlib import Path

        pa_dir = Path(__file__).resolve().parent.parent / "PriceActionAgent"
        if str(pa_dir) not in sys.path:
            sys.path.insert(0, str(pa_dir))
        from config import SETUP_PARAMS, SMART_PARAMS
    return SETUP_PARAMS, SMART_PARAMS


def _sort_by_hunt_protocol(candidates: list, permitted: set[str]) -> list:
    order = []
    if "BUY" in permitted:
        order.extend(HUNT_ORDER_BUY)
    if "SELL" in permitted:
        order.extend(HUNT_ORDER_SELL)
    rank = {s: i for i, s in enumerate(order)}
    return sorted(candidates, key=lambda x: rank.get(x.setup, 99))


def run_smart_synthesis(
    symbol: str,
    window_start: str,
    window_end: str,
    next_trading_date: str,
    weekly,
    daily,
    h4,
    anchor_block: str = "",
    anchor_metrics: dict | None = None,
    *,
    vwap_1d: float | None = None,
    vwap_4h: float | None = None,
    volume_profile_1d: dict | None = None,
    volume_profile_4h: dict | None = None,
    nifty_context: dict | None = None,
) -> dict:
    """Run deterministic synthesis; returns same JSON schema as LLM path."""
    params, smart_params = _load_config()
    anchor_metrics = anchor_metrics or {}
    scorer = make_scorer(smart_params)

    if not daily:
        return serialize_no_trade(symbol, "Insufficient daily candle data", next_trading_date)

    feats = build_features(
        daily,
        weekly,
        h4,
        anchor_metrics,
        vwap_1d=vwap_1d,
        vwap_4h=vwap_4h,
        vp_1d=volume_profile_1d,
        vp_4h=volume_profile_4h,
        nifty_rs=nifty_context,
    )
    feats.symbol = symbol
    ma_20 = float(anchor_metrics.get("ma_20d") or 0.0)

    registry = ClaimRegistry.build_chronological(daily, weekly, h4, feats, params)
    s1w = extract_state(weekly, "1W", registry, feats=feats, ma_20=ma_20, smart_params=smart_params)
    s1d = extract_state(daily, "1D", registry, feats=feats, ma_20=ma_20, smart_params=smart_params)
    s4h = extract_state(h4, "4H", registry, feats=feats, ma_20=ma_20, smart_params=smart_params)
    states = {"1w": s1w, "1d": s1d, "4h": s4h}

    reject = check_hard_disqualifiers(s1w, s1d, s4h, feats, params)
    if reject:
        log.info("%s: SmartEngine NO_TRADE — %s", symbol, reject)
        return serialize_synthesis(symbol, registry, s1w, s1d, s4h, None, reject, next_trading_date)

    permitted = permitted_directions(s1w, s1d, s4h)
    if not permitted:
        reason = "Multi-TF trend cascade permits no direction"
        return serialize_synthesis(symbol, registry, s1w, s1d, s4h, None, reason, next_trading_date)

    candidates = []
    candidates.extend(scan_all_structure(s1w, s1d, s4h, daily, registry, feats, params, permitted))
    candidates.extend(scan_all_trend(s1d, daily, feats, params, permitted))
    candidates.extend(scan_all_momentum(s1w, s1d, s4h, daily, feats, params, permitted))
    candidates = filter_signals_by_direction(candidates, permitted)
    # Correctness gate: drop signals with wrong-side or near-zero-risk stops.
    candidates = [c for c in candidates if geometry_ok(c, feats, params)]
    # Suppress disabled setups (e.g. PA_SMART_DISABLED_SETUPS=V1,P1).
    disabled = smart_params.get("disabled_setups") or set()
    if disabled:
        candidates = [c for c in candidates if c.setup not in disabled]
    candidates = _sort_by_hunt_protocol(candidates, permitted)

    best = select_best(candidates, feats, params, states, scorer, smart_params)
    if not best:
        reason = "No setup met minimum confidence, R:R and confluence gates"
        return serialize_synthesis(symbol, registry, s1w, s1d, s4h, None, reason, next_trading_date)

    for claim_id in best.claims:
        registry.add(
            layer="1D",
            claim_type="setup",
            price=best.entry,
            date=window_end,
            direction=best.direction,
            note=f"{best.setup}: {claim_id}",
        )

    log.info(
        "%s: SmartEngine %s setup=%s conf=%.2f R:R=%.2f scorer=%s",
        symbol, best.direction, best.setup, best.score, best.rr,
        smart_params.get("scorer", "rule"),
    )
    return serialize_synthesis(symbol, registry, s1w, s1d, s4h, best, None, next_trading_date)
