"""Trend setups: P1, P2, C1, C2."""

from __future__ import annotations

from SmartEngine.regime import TREND_DOWN, TREND_UP
from SmartEngine.setups.base import Signal, buffer_price, close_position_in_range, pct_of, vol_ratio


def _near_ma(price: float, ma: float, pct: float) -> bool:
    if ma <= 0:
        return False
    return abs(price - ma) / ma * 100.0 <= pct


def scan_p1_pullback_buy(s1d, daily, feats, params, permitted) -> Signal | None:
    if "BUY" not in permitted or s1d.trend != TREND_UP or not daily:
        return None
    last = daily[-1]
    if not _near_ma(last.low, feats.ma_20d, params["ma_proximity_pct"]):
        return None
    if vol_ratio(last, feats.avg_vol_20d) > params["pullback_vol_max"]:
        return None
    entry = buffer_price(last.close, params["entry_buffer_pct"], "BUY", "entry")
    stop = buffer_price(feats.ma_20d, params["ma_stop_buffer_pct"], "BUY", "stop")
    res = s1d.nearest_resistance(entry)
    target = res if res else entry + 2 * (entry - stop)
    if (target - entry) / max(entry - stop, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("P1", "BUY", round(entry, 2), round(target, 2), round(stop, 2), 2.8, ["P1_MA_PULLBACK"])


def scan_p2_pullback_sell(s1d, daily, feats, params, permitted) -> Signal | None:
    if "SELL" not in permitted or s1d.trend != TREND_DOWN or not daily:
        return None
    last = daily[-1]
    if not _near_ma(last.high, feats.ma_20d, params["ma_proximity_pct"]):
        return None
    if vol_ratio(last, feats.avg_vol_20d) > params["pullback_vol_max"]:
        return None
    entry = buffer_price(last.close, params["entry_buffer_pct"], "SELL", "entry")
    stop = buffer_price(feats.ma_20d, params["ma_stop_buffer_pct"], "SELL", "stop")
    sup = s1d.nearest_support(entry)
    target = sup if sup else entry - 2 * (stop - entry)
    if (entry - target) / max(stop - entry, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("P2", "SELL", round(entry, 2), round(target, 2), round(stop, 2), 2.8, ["P2_MA_PULLBACK"])


def _consolidation_slice(daily, params):
    for n in range(params["consolidation_max_bars"], params["consolidation_min_bars"] - 1, -1):
        if len(daily) < n:
            continue
        chunk = daily[-n:]
        hi, lo = max(b.high for b in chunk), min(b.low for b in chunk)
        mid = (hi + lo) / 2
        if mid <= 0:
            continue
        rng_pct = (hi - lo) / mid * 100
        if rng_pct > params["consolidation_max_range_pct"]:
            continue
        avg_v = sum(b.volume for b in chunk) / n
        if avg_v > 0 and chunk[-1].volume / avg_v > params["consolidation_vol_max"]:
            continue
        closes_in = sum(
            1 for b in chunk
            if close_position_in_range(b) >= 100 - params["consolidation_close_range_pct"]
            or close_position_in_range(b) <= params["consolidation_close_range_pct"]
        )
        if closes_in < n // 2:
            continue
        return chunk, hi, lo
    return None, 0.0, 0.0


def scan_c1_consolidation_breakout(s1d, daily, feats, params, permitted) -> Signal | None:
    if "BUY" not in permitted:
        return None
    chunk, hi, lo = _consolidation_slice(daily, params)
    if not chunk:
        return None
    last = daily[-1]
    if last.close <= hi or vol_ratio(last, feats.avg_vol_20d) < params["breakout_vol_min"]:
        return None
    entry = buffer_price(hi, params["entry_buffer_pct"], "BUY", "entry")
    stop = buffer_price(lo, params["entry_buffer_pct"], "BUY", "stop")
    target = entry + (hi - lo)
    if (target - entry) / max(entry - stop, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("C1", "BUY", round(entry, 2), round(target, 2), round(stop, 2), 2.6, ["C1_CONSOL_BREAKOUT"])


def scan_c2_consolidation_breakdown(s1d, daily, feats, params, permitted) -> Signal | None:
    if "SELL" not in permitted:
        return None
    chunk, hi, lo = _consolidation_slice(daily, params)
    if not chunk:
        return None
    last = daily[-1]
    if last.close >= lo or vol_ratio(last, feats.avg_vol_20d) < params["breakout_vol_min"]:
        return None
    entry = buffer_price(lo, params["entry_buffer_pct"], "SELL", "entry")
    stop = buffer_price(hi, params["entry_buffer_pct"], "SELL", "stop")
    target = entry - (hi - lo)
    if (entry - target) / max(stop - entry, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("C2", "SELL", round(entry, 2), round(target, 2), round(stop, 2), 2.6, ["C2_CONSOL_BREAKDOWN"])


def scan_all_trend(s1d, daily, feats, params, permitted) -> list:
    out = []
    for fn in (scan_p1_pullback_buy, scan_p2_pullback_sell, scan_c1_consolidation_breakout, scan_c2_consolidation_breakdown):
        sig = fn(s1d, daily, feats, params, permitted)
        if sig:
            out.append(sig)
    return out
