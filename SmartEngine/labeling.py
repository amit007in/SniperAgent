"""Build supervised training labels from historical setup replay."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, asdict

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class LabeledSignal:
    symbol: str
    decision_date: str
    setup: str
    direction: str
    entry: float
    target: float
    stop_loss: float
    outcome: str  # win | loss | timeout | open
    bars_held: int
    rr: float
    features: dict


def _walk_outcome(direction: str, entry: float, target: float, stop: float, forward_bars) -> tuple[str, int]:
    """First touch of target or stop wins."""
    for i, bar in enumerate(forward_bars, 1):
        if direction == "BUY":
            if bar.low <= stop:
                return "loss", i
            if bar.high >= target:
                return "win", i
        else:
            if bar.high >= stop:
                return "loss", i
            if bar.low <= target:
                return "win", i
    return "timeout", len(forward_bars)


def label_signal(
    symbol: str,
    decision_date: str,
    signal,
    forward_candles: list,
    timeout_bars: int = 10,
) -> LabeledSignal:
    fwd = forward_candles[:timeout_bars]
    outcome, bars = _walk_outcome(signal.direction, signal.entry, signal.target, signal.stop_loss, fwd)
    risk = abs(signal.entry - signal.stop_loss)
    reward = abs(signal.target - signal.entry)
    rr = reward / risk if risk > 1e-9 else 0.0
    return LabeledSignal(
        symbol=symbol,
        decision_date=decision_date,
        setup=signal.setup,
        direction=signal.direction,
        entry=signal.entry,
        target=signal.target,
        stop_loss=signal.stop_loss,
        outcome=outcome,
        bars_held=bars,
        rr=rr,
        features={},
    )


def extract_feature_vector(signal, states: dict, feats) -> np.ndarray:
    from SmartEngine.scorer import FEATURE_NAMES

    s1d = states.get("1d")
    s1w = states.get("1w")
    s4h = states.get("4h")
    align = 1.0 if s1w and s1d and s4h and s1w.trend == s1d.trend == s4h.trend else 0.0
    return np.array([
        signal.rr,
        feats.last_bar_vol_ratio,
        feats.mom_tstat_1d,
        feats.vr_1d,
        s1d.trend_confidence if s1d else 0.5,
        align,
        signal.score,
        feats.gap_pct,
    ])


def generate_labels_for_symbol(
    symbol: str,
    end_date: datetime.date,
    rolling_days: int = 90,
    timeout_bars: int = 10,
) -> list[LabeledSignal]:
    """Replay SmartEngine over history windows and label forward outcomes."""
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    pa = repo / "PriceActionAgent"
    if str(pa) not in sys.path:
        sys.path.insert(0, str(pa))
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    from config import SETUP_PARAMS, SMART_PARAMS
    from data_loader import get_symbol_data_window, load_nifty_daily
    from analytics import build_vwap_block, build_volume_profile_block, compute_nifty_context
    from SmartEngine.features import build_features
    from SmartEngine.registry import ClaimRegistry
    from SmartEngine.reconcile import permitted_directions, filter_signals_by_direction
    from SmartEngine.setups.structure import scan_all_structure
    from SmartEngine.setups.trend import scan_all_trend
    from SmartEngine.setups.momentum import scan_all_momentum
    from SmartEngine.state import extract_state

    labels: list[LabeledSignal] = []
    daily, weekly, h4, anchors, ws, we = get_symbol_data_window(
        symbol, end_date=end_date, rolling_days=rolling_days,
    )
    if len(daily) < 30:
        return labels

    params = SETUP_PARAMS
    smart = SMART_PARAMS

    for idx in range(25, len(daily) - timeout_bars):
        window_daily = daily[: idx + 1]
        window_weekly = [c for c in weekly if c.date <= window_daily[-1].date]
        window_h4 = [c for c in h4 if c.date <= window_daily[-1].date]
        anchor_metrics = {
            "last_close": window_daily[-1].close,
            "ma_20d": anchors.ma_20d,
            "ma_50d": anchors.ma_50d,
            "avg_vol_20d": anchors.avg_vol_20d,
        }
        nifty = load_nifty_daily(ws, we)
        vp_blk = build_volume_profile_block(window_daily)
        vw = build_vwap_block(window_daily, [], window_daily[-1].close)
        nc = compute_nifty_context(window_daily, nifty) if nifty else None

        feats = build_features(
            window_daily, window_weekly, window_h4, anchor_metrics,
            vwap_1d=vw.get("session_vwap"),
            vp_1d=(vp_blk or {}).get("20d"),
            nifty_rs=nc,
        )
        registry = ClaimRegistry.build_chronological(window_daily, window_weekly, window_h4, feats, params)
        s1w = extract_state(window_weekly, "1W", registry, feats=feats, ma_20=anchors.ma_20d, smart_params=smart)
        s1d = extract_state(window_daily, "1D", registry, feats=feats, ma_20=anchors.ma_20d, smart_params=smart)
        s4h = extract_state(window_h4, "4H", registry, feats=feats, ma_20=anchors.ma_20d, smart_params=smart)

        permitted = permitted_directions(s1w, s1d, s4h)
        if not permitted:
            continue

        candidates = []
        candidates.extend(scan_all_structure(s1w, s1d, s4h, window_daily, registry, feats, params, permitted))
        candidates.extend(scan_all_trend(s1d, window_daily, feats, params, permitted))
        candidates.extend(scan_all_momentum(s1w, s1d, s4h, window_daily, feats, params, permitted))
        candidates = filter_signals_by_direction(candidates, permitted)

        for sig in candidates:
            lab = label_signal(
                symbol,
                str(window_daily[-1].date),
                sig,
                daily[idx + 1 : idx + 1 + timeout_bars],
                timeout_bars,
            )
            states = {"1w": s1w, "1d": s1d, "4h": s4h}
            lab.features = dict(zip(
                ["rr", "vol_ratio", "mom_tstat", "vr_1d", "trend_conf", "align", "setup_base", "gap_pct"],
                extract_feature_vector(sig, states, feats).tolist(),
            ))
            labels.append(lab)

    return labels


def labels_to_arrays(labels: list[LabeledSignal]) -> tuple[np.ndarray, np.ndarray]:
    X, y = [], []
    for lab in labels:
        if lab.outcome not in ("win", "loss"):
            continue
        if not lab.features:
            continue
        X.append(list(lab.features.values()))
        y.append(1 if lab.outcome == "win" else 0)
    if not X:
        return np.empty((0, 8)), np.empty((0,))
    return np.array(X), np.array(y)


def save_labels(labels: list[LabeledSignal], path) -> None:
    import json
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([asdict(l) for l in labels], indent=2))
