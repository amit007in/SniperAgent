"""Shared quantitative helpers for SmartEngine and SmartAgent."""

from __future__ import annotations

import numpy as np
import pandas as pd

EWMA_LAMBDA = 0.94
VOL_Z_WINDOW = 20
MOM_WINDOW = 20
VR_Q = 5
PROFILE_BINS = 50
VALUE_AREA_PCT = 0.70


def candles_to_df(candles) -> pd.DataFrame:
    """Convert PriceActionAgent Candle list to OHLCV DataFrame."""
    if not candles:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(
        {
            "date": [c.date for c in candles],
            "open": [c.open for c in candles],
            "high": [c.high for c in candles],
            "low": [c.low for c in candles],
            "close": [c.close for c in candles],
            "volume": [c.volume for c in candles],
        }
    )


def wilder_atr(df: pd.DataFrame, period: int) -> float:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr = pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]
    return float(max(atr, 1e-9))


def ewma_realised_vol(df: pd.DataFrame, ann_factor: float, lam: float = EWMA_LAMBDA) -> float:
    c = df["close"].values.astype(float)
    if len(c) < 3:
        return 0.0
    r = np.diff(np.log(np.maximum(c, 1e-9)))
    sigma2 = r[0] ** 2
    for x in r[1:]:
        sigma2 = lam * sigma2 + (1.0 - lam) * x * x
    return float(np.sqrt(sigma2 * ann_factor) * 100.0)


def volume_zscore(df: pd.DataFrame, window: int = VOL_Z_WINDOW) -> float:
    v = df["volume"].astype(float)
    if len(v) < window + 1:
        return 0.0
    mu = v.rolling(window).mean().iloc[-1]
    sd = v.rolling(window).std().iloc[-1]
    if not np.isfinite(sd) or sd < 1e-9:
        return 0.0
    return float((v.iloc[-1] - mu) / sd)


def momentum_tstat(df: pd.DataFrame, window: int = MOM_WINDOW) -> float:
    c = df["close"].values.astype(float)
    if len(c) < window + 1:
        return 0.0
    r = np.diff(np.log(np.maximum(c[-(window + 1) :], 1e-9)))
    sd = r.std(ddof=1)
    if sd < 1e-12:
        return 0.0
    return float(r.mean() / (sd / np.sqrt(len(r))))


def variance_ratio(df: pd.DataFrame, q: int = VR_Q, window: int = 80) -> float:
    c = df["close"].values.astype(float)
    if len(c) < max(window, 4 * q) + 1:
        return 0.0
    r = np.diff(np.log(np.maximum(c[-(window + 1) :], 1e-9)))
    v1 = r.var(ddof=1)
    if v1 < 1e-16:
        return 0.0
    rq = np.convolve(r, np.ones(q), mode="valid")
    vq = rq.var(ddof=1)
    vr = vq / (q * v1)
    return float(np.log(max(vr, 1e-6)))


def volume_profile(
    df: pd.DataFrame,
    n_bins: int = PROFILE_BINS,
    va_pct: float = VALUE_AREA_PCT,
) -> dict[str, float]:
    if df.empty or len(df) < 10:
        return {"poc": 0.0, "vah": 0.0, "val": 0.0}
    lo, hi = df["low"].min(), df["high"].max()
    if hi - lo < 1e-9:
        p = float(df["close"].iloc[-1])
        return {"poc": p, "vah": p, "val": p}
    edges = np.linspace(lo, hi, n_bins + 1)
    hist = np.zeros(n_bins)
    for h, l, v in zip(df["high"].values, df["low"].values, df["volume"].values):
        if h - l < 1e-9:
            idx = min(np.searchsorted(edges, l, side="right") - 1, n_bins - 1)
            hist[max(idx, 0)] += v
            continue
        ov_lo = np.maximum(edges[:-1], l)
        ov_hi = np.minimum(edges[1:], h)
        hist += v * np.clip(ov_hi - ov_lo, 0.0, None) / (h - l)
    poc_i = int(np.argmax(hist))
    poc = 0.5 * (edges[poc_i] + edges[poc_i + 1])
    total, captured = hist.sum(), hist[poc_i]
    lo_i = hi_i = poc_i
    while captured < va_pct * total:
        v_dn = hist[lo_i - 1] if lo_i > 0 else -1.0
        v_up = hist[hi_i + 1] if hi_i < n_bins - 1 else -1.0
        if v_dn < 0 and v_up < 0:
            break
        if v_up >= v_dn:
            hi_i += 1
            captured += hist[hi_i]
        else:
            lo_i -= 1
            captured += hist[lo_i]
    return {"poc": float(poc), "vah": float(edges[hi_i + 1]), "val": float(edges[lo_i])}


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35, 35)))


def kelly_fraction(p: float, beta: float) -> float:
    return max(0.0, p - (1.0 - p) / max(beta, 1e-9))
