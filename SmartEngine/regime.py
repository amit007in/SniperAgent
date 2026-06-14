"""Trend / regime classification — structural rules + statistical confirmation + optional HMM."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from SmartAgent.quantcore import candles_to_df, momentum_tstat, variance_ratio, volume_zscore

TREND_UP = "UPTREND"
TREND_DOWN = "DOWNTREND"
TREND_SIDE = "SIDEWAYS"
TREND_TRANS = "TRANSITIONING"

PHASE_MARKUP = "MARKUP"
PHASE_MARKDOWN = "MARKDOWN"
PHASE_ACCUM = "ACCUMULATION"
PHASE_DIST = "DISTRIBUTION"
PHASE_CONSOL = "CONSOLIDATION"


@dataclass
class RegimeResult:
    trend: str
    confidence: float
    ambiguous: bool = False
    phase: str = PHASE_CONSOL
    vol_character: str = "NEUTRAL"
    mom_tstat: float = 0.0
    variance_ratio: float = 0.0
    hmm_state: int | None = None


def regression_slope_tstat(candles) -> tuple[float, float]:
    """OLS of log-close on time. Returns (slope_pct_per_bar, t_stat).

    The t-stat measures how *significant* the directional drift is — large |t|
    means a clean, persistent trend; near-zero means chop. This is the missing
    'is the line actually going down?' signal that pivot counts ignore.
    """
    if not candles or len(candles) < 8:
        return 0.0, 0.0
    closes = np.array([c.close for c in candles], dtype=float)
    closes = closes[closes > 0]
    if len(closes) < 8:
        return 0.0, 0.0
    y = np.log(closes)
    x = np.arange(len(y), dtype=float)
    n = len(y)
    sx, sy = x.mean(), y.mean()
    sxx = ((x - sx) ** 2).sum()
    if sxx <= 0:
        return 0.0, 0.0
    slope = ((x - sx) * (y - sy)).sum() / sxx
    intercept = sy - slope * sx
    resid = y - (intercept + slope * x)
    dof = n - 2
    if dof <= 0:
        return 0.0, 0.0
    se = np.sqrt((resid ** 2).sum() / dof / sxx)
    t = slope / se if se > 1e-12 else 0.0
    return float(slope * 100.0), float(t)


def classify_trend_structural(
    higher_highs: int,
    higher_lows: int,
    lower_highs: int,
    lower_lows: int,
) -> str:
    bull = higher_highs >= 2 and higher_lows >= 2
    bear = lower_highs >= 2 and lower_lows >= 2
    if bull and not bear:
        return TREND_UP
    if bear and not bull:
        return TREND_DOWN
    if bull and bear:
        return TREND_TRANS
    return TREND_SIDE


def _infer_phase(trend: str, mom: float, vr: float, close_vs_ma: float) -> str:
    if trend == TREND_UP and mom > 0.5:
        return PHASE_MARKUP
    if trend == TREND_DOWN and mom < -0.5:
        return PHASE_MARKDOWN
    if abs(vr) < 0.1 and abs(mom) < 0.3:
        return PHASE_CONSOL
    if close_vs_ma > 0 and vr < 0:
        return PHASE_ACCUM
    if close_vs_ma < 0 and vr < 0:
        return PHASE_DIST
    return PHASE_CONSOL


def _vol_character(mom: float, vol_z: float) -> str:
    if vol_z > 1.0 and mom > 0:
        return "ACCUMULATION"
    if vol_z > 1.0 and mom < 0:
        return "DISTRIBUTION"
    if vol_z > 0.5:
        return "MIXED"
    return "NEUTRAL"


def _hmm_posterior(candles, n_states: int = 4) -> tuple[np.ndarray | None, float]:
    """Optional Gaussian HMM; returns (posterior row for last bar, max prob)."""
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError:
        return None, 0.0

    if len(candles) < 30:
        return None, 0.0

    df = candles_to_df(candles)
    c = df["close"].values.astype(float)
    v = df["volume"].values.astype(float)
    r = np.diff(np.log(np.maximum(c, 1e-9)))
    if len(r) < 20:
        return None, 0.0
    abs_r = np.abs(r)
    vol_z = np.zeros(len(r))
    for i in range(10, len(r)):
        w = v[i - 9 : i + 1]
        mu, sd = w.mean(), w.std()
        vol_z[i] = (v[i] - mu) / sd if sd > 1e-9 else 0.0
    X = np.column_stack([r[10:], abs_r[10:], vol_z[10:]])
    try:
        model = GaussianHMM(n_components=n_states, covariance_type="diag", n_iter=50, random_state=42)
        model.fit(X)
        post = model.predict_proba(X)
        return post, float(post[-1].max())
    except Exception:
        return None, 0.0


def _transitioning_from_hmm(post: np.ndarray, tau_low: float) -> bool:
    """True when probability mass is shifting between range and trend states."""
    if post is None or len(post) < 5:
        return False
    recent = post[-5:]
    flux = float(np.mean(np.abs(np.diff(recent, axis=0)).sum(axis=1)))
    return flux > tau_low * 0.5


def _finish(trend, confidence, ambiguous, candles, mom, vr, vol_z, ma_20,
            params, use_hmm, tau_high, tau_low) -> RegimeResult:
    """Shared tail: optional HMM adjustment, phase, vol character, build result."""
    close = candles[-1].close if candles else 0.0
    close_vs_ma = (close - ma_20) / ma_20 if ma_20 > 0 else 0.0

    post, hmm_max = None, 0.0
    if use_hmm and candles:
        post, hmm_max = _hmm_posterior(candles, params.get("hmm_states", 4))
        if post is not None:
            if hmm_max >= tau_high:
                confidence = max(confidence, hmm_max)
            elif hmm_max < tau_low:
                ambiguous = True
            if _transitioning_from_hmm(post, tau_low) and trend in (TREND_SIDE, TREND_TRANS):
                trend = TREND_TRANS
                confidence = max(confidence, 0.5)

    return RegimeResult(
        trend=trend,
        confidence=round(confidence, 3),
        ambiguous=ambiguous,
        phase=_infer_phase(trend, mom, vr, close_vs_ma),
        vol_character=_vol_character(mom, vol_z),
        mom_tstat=mom,
        variance_ratio=vr,
        hmm_state=int(post[-1].argmax()) if post is not None else None,
    )


def resolve_regime(
    candles,
    structural: tuple[int, int, int, int],
    *,
    ma_20: float = 0.0,
    smart_params: dict | None = None,
) -> RegimeResult:
    """Combine structural pivots, momentum t-stat, variance ratio, optional HMM."""
    params = smart_params or {}
    tau_high = params.get("regime_tau_high", 0.60)
    tau_low = params.get("regime_tau_low", 0.40)
    use_hmm = params.get("use_hmm", False)

    slope_t_thresh = params.get("regime_slope_tstat", 2.0)

    hh, hl, lh, ll = structural
    trend = classify_trend_structural(hh, hl, lh, ll)
    df = candles_to_df(candles) if candles else None
    mom = momentum_tstat(df) if df is not None and len(df) > 5 else 0.0
    vr = variance_ratio(df) if df is not None and len(df) > 5 else 0.0
    vol_z = volume_zscore(df) if df is not None and len(df) > 5 else 0.0
    slope_pct, slope_t = regression_slope_tstat(candles)

    # Co-primary directional override: if the regression slope is statistically
    # significant, it sets the trend directly — a steadily declining window must
    # be DOWNTREND even when pivot counts default to SIDEWAYS (which would wrongly
    # permit longs via the cascade). Pivots still win when they AGREE or when the
    # slope is insignificant; a slope that CONTRADICTS clean pivots → TRANSITIONING.
    if slope_t <= -slope_t_thresh:
        if trend in (TREND_SIDE, TREND_TRANS, TREND_DOWN):
            trend = TREND_DOWN
            confidence = min(0.95, 0.6 + min(abs(slope_t) / 10.0, 0.3))
            return _finish(trend, confidence, False, candles, mom, vr, vol_z, ma_20,
                           params, use_hmm, tau_high, tau_low)
        else:  # clean bullish pivots but down slope → genuine conflict
            trend = TREND_TRANS
            return _finish(trend, 0.5, True, candles, mom, vr, vol_z, ma_20,
                           params, use_hmm, tau_high, tau_low)
    if slope_t >= slope_t_thresh:
        if trend in (TREND_SIDE, TREND_TRANS, TREND_UP):
            trend = TREND_UP
            confidence = min(0.95, 0.6 + min(abs(slope_t) / 10.0, 0.3))
            return _finish(trend, confidence, False, candles, mom, vr, vol_z, ma_20,
                           params, use_hmm, tau_high, tau_low)
        else:
            trend = TREND_TRANS
            return _finish(trend, 0.5, True, candles, mom, vr, vol_z, ma_20,
                           params, use_hmm, tau_high, tau_low)

    confidence = 0.5
    ambiguous = False

    if trend == TREND_SIDE:
        if abs(mom) < 0.3 and abs(vr) < 0.15:
            confidence = 0.7
        elif abs(mom) > 0.8 or abs(vr) > 0.3:
            trend = TREND_TRANS
            confidence = 0.55
        else:
            trend = TREND_TRANS
            confidence = 0.45
            ambiguous = True
    elif trend == TREND_UP:
        confidence = min(0.95, 0.55 + abs(mom) * 0.1 + max(0.0, vr) * 0.15)
        if mom < -0.5 or vr < -0.2:
            trend = TREND_TRANS
            confidence = 0.5
            ambiguous = True
    elif trend == TREND_DOWN:
        confidence = min(0.95, 0.55 + abs(mom) * 0.1 + max(0.0, -vr) * 0.15)
        if mom > 0.5 or vr > 0.2:
            trend = TREND_TRANS
            confidence = 0.5
            ambiguous = True

    return _finish(trend, confidence, ambiguous, candles, mom, vr, vol_z, ma_20,
                   params, use_hmm, tau_high, tau_low)


def alignment_label(t1w: str, t1d: str, t4h: str) -> str:
    """Schema-aligned alignment labels from prompts.py."""
    ups = sum(1 for t in (t1w, t1d, t4h) if t == TREND_UP)
    downs = sum(1 for t in (t1w, t1d, t4h) if t == TREND_DOWN)
    if ups == 3:
        return "ALIGNED_BULLISH"
    if downs == 3:
        return "ALIGNED_BEARISH"
    if ups > 0 and downs > 0:
        return "CONFLICTED"
    if ups >= 2 or downs >= 2:
        return "MIXED"
    return "MIXED"
