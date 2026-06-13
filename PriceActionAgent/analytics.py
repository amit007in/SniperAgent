"""
PriceActionAgent — Quantitative Analytics (Phase 2)
=====================================================
Computation functions for anchor-block enrichment.

  • VWAP    — session VWAP + anchored VWAP + slope  (4H layer)
  • Volume Profile — POC / VAH / VAL / HVN / LVN     (1D and 1W layers)
  • Nifty RS — relative strength vs Nifty 50          (all layers)

All functions are pure (no I/O, no DB calls).
They accept lists of Candle objects from data_loader.py.
"""
import math
from typing import NamedTuple


# ---------------------------------------------------------------------------
# VWAP — 4H Layer
# ---------------------------------------------------------------------------

def compute_session_vwap(candles: list) -> float | None:
    """
    VWAP over a list of Candle objects (30-min intraday bars or daily bars).
    Formula: cumulative(typical_price × volume) / cumulative(volume)
    Returns None if candles is empty or total volume is zero.
    """
    if not candles:
        return None
    total_tp_vol = sum(
        ((c.high + c.low + c.close) / 3.0) * c.volume
        for c in candles
    )
    total_vol = sum(c.volume for c in candles)
    if total_vol == 0:
        return None
    return round(total_tp_vol / total_vol, 2)


def compute_anchored_vwap(daily_candles: list, anchor_date: str) -> float | None:
    """
    VWAP anchored from anchor_date (YYYY-MM-DD, inclusive) through end of candles.
    Useful for anchoring to a swing low / swing high for multi-day structural context.
    """
    anchored = [c for c in daily_candles if c.date >= anchor_date]
    return compute_session_vwap(anchored)


def compute_vwap_slope(daily_candles: list, lookback: int = 3) -> str:
    """
    Direction of the 1D-VWAP over the last `lookback` sessions.
    Returns: "rising" | "falling" | "flat"
    Threshold: > 0.1% change over lookback period = trending.
    """
    if len(daily_candles) < lookback + 1:
        return "flat"
    recent = daily_candles[-(lookback + 1):]
    # Compute single-bar VWAP for each day in the window
    vwaps = [compute_session_vwap([c]) for c in recent]
    vwaps = [v for v in vwaps if v is not None]
    if len(vwaps) < 2:
        return "flat"
    delta = vwaps[-1] - vwaps[0]
    threshold = vwaps[0] * 0.001  # 0.1%
    if delta > threshold:
        return "rising"
    if delta < -threshold:
        return "falling"
    return "flat"


def build_vwap_block(
    daily_candles: list,        # last ~20 days of daily candles
    intraday_candles: list,     # today's 30-min candles (for session VWAP)
    current_price: float,
    swing_low_date: str | None = None,  # anchor date for anchored VWAP
) -> dict:
    """
    Build the full VWAP data block for the anchor section.
    Returns a dict safe to pass to build_anchor_block(vwap_4h=...).
    """
    session_vwap = compute_session_vwap(intraday_candles or daily_candles[-1:])
    prev_session_vwap = compute_session_vwap(
        daily_candles[-2:-1]) if len(daily_candles) >= 2 else None
    slope = compute_vwap_slope(daily_candles)
    anchored = None
    if swing_low_date:
        anchored = compute_anchored_vwap(daily_candles, swing_low_date)

    vs_pct = None
    if session_vwap and current_price:
        vs_pct = round((current_price - session_vwap) / session_vwap * 100, 2)

    result = {
        "session_vwap":                   session_vwap,
        "current_vs_session_vwap_pct":    vs_pct,
        "prev_session_vwap":              prev_session_vwap,
        "vwap_slope":                     slope,
    }
    if anchored is not None:
        result["anchored_vwap_from_swing_low"] = anchored
    return result


# ---------------------------------------------------------------------------
# Volume Profile — 1D and 1W Layers
# ---------------------------------------------------------------------------

class VolumeProfile(NamedTuple):
    poc:                  float        # Point of Control
    vah:                  float        # Value Area High  (70% volume)
    val:                  float        # Value Area Low   (70% volume)
    hvn_levels:           list[float]  # High Volume Nodes (top 5) — strong S/R
    lvn_levels:           list[float]  # Low Volume Nodes (inside VA) — price vacuum
    value_area_width_pct: float        # (VAH - VAL) / POC × 100


def compute_volume_profile(candles: list, n_bins: int = 100) -> VolumeProfile | None:
    """
    Approximate volume profile from OHLCV Candle objects.

    Method: distribute each bar's volume uniformly across its high-low range
    into n_bins price buckets. Accurate to ~1-2% of true tick-data POC for
    liquid NSE stocks (sufficient for structural analysis).

    Args:
        candles: list of Candle objects (daily, weekly, or aggregated)
        n_bins:  price bucket resolution (100 ≈ 0.5-1% per bucket for most stocks)

    Returns VolumeProfile or None if data is insufficient.
    """
    if not candles or len(candles) < 3:
        return None

    price_min = min(c.low  for c in candles)
    price_max = max(c.high for c in candles)
    if price_max <= price_min:
        return None

    bin_width   = (price_max - price_min) / n_bins
    bins_lo     = [price_min + i       * bin_width for i in range(n_bins)]
    bins_hi     = [price_min + (i + 1) * bin_width for i in range(n_bins)]
    bin_mid     = [(bins_lo[i] + bins_hi[i]) / 2   for i in range(n_bins)]
    vol_profile = [0.0] * n_bins

    for c in candles:
        bar_range = c.high - c.low
        if bar_range < 1e-8:
            # Doji / flat bar — all volume to nearest bin
            idx = min(int((c.close - price_min) / bin_width), n_bins - 1)
            idx = max(0, idx)
            vol_profile[idx] += c.volume
            continue
        for i in range(n_bins):
            overlap = max(0.0, min(c.high, bins_hi[i]) - max(c.low, bins_lo[i]))
            vol_profile[i] += c.volume * (overlap / bar_range)

    total_vol = sum(vol_profile)
    if total_vol == 0:
        return None

    # POC — bin with maximum volume
    poc_idx = vol_profile.index(max(vol_profile))
    poc     = round(bin_mid[poc_idx], 2)

    # Value Area — expand from POC outward until 70% of total volume covered
    target    = total_vol * 0.70
    sorted_i  = sorted(range(n_bins), key=lambda i: vol_profile[i], reverse=True)
    running   = 0.0
    va_set: set[int] = set()
    for idx in sorted_i:
        running += vol_profile[idx]
        va_set.add(idx)
        if running >= target:
            break

    vah = round(bin_mid[max(va_set)], 2)
    val = round(bin_mid[min(va_set)], 2)

    # HVN — bins with volume > 1.5× mean (top 5, sorted price desc)
    mean_vol = total_vol / n_bins
    hvn = sorted(
        [round(bin_mid[i], 2) for i in range(n_bins)
         if vol_profile[i] > 1.5 * mean_vol],
        reverse=True
    )[:5]

    # LVN — bins with volume < 0.4× mean, inside value area only (top 3, price desc)
    lvn = sorted(
        [round(bin_mid[i], 2) for i in range(n_bins)
         if vol_profile[i] < 0.4 * mean_vol
         and val <= bin_mid[i] <= vah],
        reverse=True
    )[:3]

    va_width = round((vah - val) / poc * 100, 2) if poc > 0 else 0.0

    return VolumeProfile(
        poc=poc, vah=vah, val=val,
        hvn_levels=hvn, lvn_levels=lvn,
        value_area_width_pct=va_width,
    )


def build_volume_profile_block(
    daily_candles: list,
) -> dict:
    """
    Build the volume_profile block for the anchor section.
    Computes both 20-day and quarterly profiles from provided daily candles.
    Returns a dict safe to pass to build_anchor_block(volume_profile=...).
    """
    result = {}

    vp_20d = compute_volume_profile(daily_candles[-20:], n_bins=100)
    if vp_20d:
        result["20d"] = {
            "poc":                  vp_20d.poc,
            "vah":                  vp_20d.vah,
            "val":                  vp_20d.val,
            "hvn_levels":           vp_20d.hvn_levels,
            "lvn_levels":           vp_20d.lvn_levels,
            "value_area_width_pct": vp_20d.value_area_width_pct,
        }

    vp_qtr = compute_volume_profile(daily_candles[-63:], n_bins=150)
    if vp_qtr:
        result["quarterly"] = {
            "poc":                  vp_qtr.poc,
            "vah":                  vp_qtr.vah,
            "val":                  vp_qtr.val,
            "hvn_levels":           vp_qtr.hvn_levels,
            "lvn_levels":           vp_qtr.lvn_levels,
            "value_area_width_pct": vp_qtr.value_area_width_pct,
        }

    return result if result else None


# ---------------------------------------------------------------------------
# Nifty 50 Relative Strength
# ---------------------------------------------------------------------------

def _pct_change(candles: list, n: int) -> float:
    """% price change over last n sessions."""
    closes = [c.close for c in candles]
    if len(closes) < n + 1:
        return 0.0
    old = closes[-(n + 1)]
    new = closes[-1]
    return round((new - old) / old * 100, 2) if old > 0 else 0.0


def _log_returns(candles: list, n: int) -> list[float]:
    """Last n log-returns from daily closes."""
    closes = [c.close for c in candles]
    rets = []
    start = max(1, len(closes) - n)
    for i in range(start, len(closes)):
        if closes[i - 1] > 0:
            rets.append(math.log(closes[i] / closes[i - 1]))
    return rets


def compute_nifty_context(
    stock_daily: list,
    nifty_daily: list,
    beta_lookback: int = 30,
) -> dict | None:
    """
    Compute stock vs Nifty 50 relative strength.

    Args:
        stock_daily:    list of Candle for the stock (daily bars)
        nifty_daily:    list of Candle for Nifty 50  (daily bars)
        beta_lookback:  sessions for rolling beta calculation (default 30)

    Returns dict or None if data is insufficient.
    """
    min_required = max(6, beta_lookback)
    if len(stock_daily) < min_required or len(nifty_daily) < min_required:
        return None

    stock_1d = _pct_change(stock_daily, 1)
    stock_1w = _pct_change(stock_daily, 5)
    nifty_1d = _pct_change(nifty_daily, 1)
    nifty_1w = _pct_change(nifty_daily, 5)

    # Rolling beta
    sr   = _log_returns(stock_daily, beta_lookback)
    nr   = _log_returns(nifty_daily, beta_lookback)
    n    = min(len(sr), len(nr))
    beta = None
    if n >= 10:
        sr, nr   = sr[-n:], nr[-n:]
        mean_s   = sum(sr) / n
        mean_n   = sum(nr) / n
        cov      = sum((sr[i] - mean_s) * (nr[i] - mean_n) for i in range(n)) / n
        var_n    = sum((nr[i] - mean_n) ** 2 for i in range(n)) / n
        beta     = round(cov / var_n, 2) if var_n > 1e-10 else None

    # Nifty trend: 5D MA vs 20D MA of Nifty closes
    nc = [c.close for c in nifty_daily]
    if len(nc) >= 20:
        ma5  = sum(nc[-5:])  / 5
        ma20 = sum(nc[-20:]) / 20
        if ma5 > ma20 * 1.002:
            nifty_trend = "up"
        elif ma5 < ma20 * 0.998:
            nifty_trend = "down"
        else:
            nifty_trend = "sideways"
    else:
        nifty_trend = "unknown"

    return {
        "stock_1d_pct":          stock_1d,
        "stock_1w_pct":          stock_1w,
        "nifty_1d_pct":          nifty_1d,
        "nifty_1w_pct":          nifty_1w,
        "stock_vs_nifty_1d_pct": round(stock_1d - nifty_1d, 2),
        "stock_vs_nifty_1w_pct": round(stock_1w - nifty_1w, 2),
        "nifty_trend":           nifty_trend,
        "beta_30d":              beta,
    }
