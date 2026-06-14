"""Structure setups: B1, B2, S1, S2, T1, T2."""

from __future__ import annotations

from SmartEngine.regime import TREND_TRANS
from SmartEngine.setups.base import (
    Signal,
    buffer_price,
    close_position_in_range,
    pct_of,
    vol_ratio,
    wick_ratio,
)


def scan_b1_breakout(
    s1w,
    s1d,
    daily,
    feats,
    params: dict,
    permitted: set[str],
) -> Signal | None:
    # Structure setups require a directional/sideways weekly — TRANS is the
    # M1/T2 carve-out's domain only (see reconcile.permitted_directions).
    if "BUY" not in permitted or s1w.trend == TREND_TRANS:
        return None
    if len(daily) < params["breakout_lookback_bars"] + 1:
        return None
    last = daily[-1]
    lookback = daily[-(params["breakout_lookback_bars"] + 1) : -1]
    period_high = max(b.high for b in lookback)
    if last.close <= period_high:
        return None
    if vol_ratio(last, feats.avg_vol_20d) < params["breakout_vol_min"]:
        return None
    entry = buffer_price(period_high, params["entry_buffer_pct"], "BUY", "entry")
    stop = buffer_price(last.low, params["entry_buffer_pct"], "BUY", "stop")
    height = period_high - min(b.low for b in lookback)
    target = entry + height
    if target <= entry or (target - entry) / max(entry - stop, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal(
        setup="B1",
        direction="BUY",
        entry=round(entry, 2),
        target=round(target, 2),
        stop_loss=round(stop, 2),
        score=3.0,
        claims=["B1_BREAKOUT_PERIOD_HIGH"],
    )


def scan_b2_breakdown(
    s1w,
    s1d,
    daily,
    feats,
    params: dict,
    permitted: set[str],
) -> Signal | None:
    if "SELL" not in permitted or s1w.trend == TREND_TRANS:
        return None
    if len(daily) < params["breakout_lookback_bars"] + 1:
        return None
    last = daily[-1]
    lookback = daily[-(params["breakout_lookback_bars"] + 1) : -1]
    period_low = min(b.low for b in lookback)
    if last.close >= period_low:
        return None
    if vol_ratio(last, feats.avg_vol_20d) < params["breakout_vol_min"]:
        return None
    entry = buffer_price(period_low, params["entry_buffer_pct"], "SELL", "entry")
    stop = buffer_price(last.high, params["entry_buffer_pct"], "SELL", "stop")
    height = max(b.high for b in lookback) - period_low
    target = entry - height
    if stop <= entry or (entry - target) / max(stop - entry, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal(
        setup="B2",
        direction="SELL",
        entry=round(entry, 2),
        target=round(target, 2),
        stop_loss=round(stop, 2),
        score=3.0,
        claims=["B2_BREAKDOWN_PERIOD_LOW"],
    )


def scan_s1_support_bounce(
    s1w,
    s1d,
    daily,
    registry,
    feats,
    params: dict,
    permitted: set[str],
) -> Signal | None:
    if "BUY" not in permitted or s1w.trend == TREND_TRANS or not daily:
        return None
    last = daily[-1]
    sup = s1d.nearest_support(last.close)
    if sup is None:
        return None
    tests = registry.count_tests(sup, daily, params["sr_zone_tolerance_pct"])
    if tests < params["min_sr_tests"]:
        return None
    if wick_ratio(last, "lower") < params["rejection_wick_ratio"]:
        return None
    if vol_ratio(last, feats.avg_vol_20d) < params["min_setup_vol"]:
        return None
    entry = buffer_price(last.close, params["entry_buffer_pct"], "BUY", "entry")
    stop = buffer_price(sup, params["entry_buffer_pct"], "BUY", "stop")
    res = s1d.nearest_resistance(entry)
    target = res if res else entry + 2 * (entry - stop)
    if (target - entry) / max(entry - stop, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal(
        setup="S1",
        direction="BUY",
        entry=round(entry, 2),
        target=round(target, 2),
        stop_loss=round(stop, 2),
        score=2.5,
        claims=["S1_SUPPORT_BOUNCE"],
    )


def scan_s2_resistance_reject(
    s1w,
    s1d,
    daily,
    registry,
    feats,
    params: dict,
    permitted: set[str],
) -> Signal | None:
    if "SELL" not in permitted or s1w.trend == TREND_TRANS or not daily:
        return None
    last = daily[-1]
    res = s1d.nearest_resistance(last.close)
    if res is None:
        return None
    tests = registry.count_tests(res, daily, params["sr_zone_tolerance_pct"])
    if tests < params["min_sr_tests"]:
        return None
    if wick_ratio(last, "upper") < params["rejection_wick_ratio"]:
        return None
    if vol_ratio(last, feats.avg_vol_20d) < params["min_setup_vol"]:
        return None
    entry = buffer_price(last.close, params["entry_buffer_pct"], "SELL", "entry")
    stop = buffer_price(res, params["entry_buffer_pct"], "SELL", "stop")
    sup = s1d.nearest_support(entry)
    target = sup if sup else entry - 2 * (stop - entry)
    if (entry - target) / max(stop - entry, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal(
        setup="S2",
        direction="SELL",
        entry=round(entry, 2),
        target=round(target, 2),
        stop_loss=round(stop, 2),
        score=2.5,
        claims=["S2_RESISTANCE_REJECT"],
    )


def scan_t1_bull_trap(
    s1w,
    s1d,
    daily,
    feats,
    params: dict,
    permitted: set[str],
) -> Signal | None:
    if "SELL" not in permitted or s1w.trend == TREND_TRANS:
        return None
    if len(daily) < params["trap_lookback_bars"] + 1:
        return None
    last = daily[-1]
    prior = daily[-(params["trap_lookback_bars"] + 1) : -1]
    broke_above = any(b.high > s1d.period_high for b in prior[:-1])
    if not broke_above or last.close >= s1d.period_high:
        return None
    entry = buffer_price(last.close, params["entry_buffer_pct"], "SELL", "entry")
    stop = buffer_price(max(b.high for b in prior), params["entry_buffer_pct"], "SELL", "stop")
    target = entry - (stop - entry)
    return Signal(
        setup="T1",
        direction="SELL",
        entry=round(entry, 2),
        target=round(target, 2),
        stop_loss=round(stop, 2),
        score=2.0,
        claims=["T1_BULL_TRAP"],
    )


def scan_t2_bear_trap(
    s1w,
    s1d,
    daily,
    feats,
    params: dict,
    permitted: set[str],
) -> Signal | None:
    if "BUY" not in permitted or s1w.trend == TREND_TRANS:
        return None
    if len(daily) < params["trap_lookback_bars"] + 1:
        return None
    last = daily[-1]
    prior = daily[-(params["trap_lookback_bars"] + 1) : -1]
    broke_below = any(b.low < s1d.period_low for b in prior[:-1])
    if not broke_below or last.close <= s1d.period_low:
        return None
    entry = buffer_price(last.close, params["entry_buffer_pct"], "BUY", "entry")
    stop = buffer_price(min(b.low for b in prior), params["entry_buffer_pct"], "BUY", "stop")
    target = entry + (entry - stop)
    return Signal(
        setup="T2",
        direction="BUY",
        entry=round(entry, 2),
        target=round(target, 2),
        stop_loss=round(stop, 2),
        score=2.0,
        claims=["T2_BEAR_TRAP"],
    )


def scan_b3_false_breakdown_reclaim(
    s1w, s1d, daily, registry, feats, params, permitted,
) -> Signal | None:
    if "BUY" not in permitted or s1w.trend == "DOWNTREND" or not daily:
        return None
    hit = registry.recent_false_breakdown(daily, params["trap_lookback_bars"], params["sr_zone_tolerance_pct"])
    if not hit:
        return None
    idx, trap_bar = hit
    last = daily[-1]
    if last.close <= trap_bar.high:
        return None
    entry = buffer_price(trap_bar.high, params["entry_buffer_pct"], "BUY", "entry")
    stop = trap_bar.low
    target = s1d.nearest_resistance(entry) or entry + 2 * (entry - stop)
    if (target - entry) / max(entry - stop, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("B3", "BUY", round(entry, 2), round(target, 2), round(stop, 2), 2.8, ["B3_FALSE_BREAKDOWN"])


def scan_b4_val_bounce(s1w, s1d, s4h, daily, feats, params, permitted) -> Signal | None:
    if "BUY" not in permitted or not daily or not feats.vp_1d:
        return None
    if s1w.trend == "DOWNTREND" or s1d.trend == "DOWNTREND":
        return None
    val = feats.vp_1d.get("val")
    if not val:
        return None
    last = daily[-1]
    if last.low > val * 1.005 or last.close <= val:
        return None
    if vol_ratio(last, feats.avg_vol_20d) < params["value_area_vol_min"]:
        return None
    if s4h.vwap_position == "BELOW" and feats.vwap_1d and last.close < feats.vwap_1d:
        return None
    entry = val
    stop = buffer_price(val, params["value_area_stop_buffer_pct"], "BUY", "stop")
    target = feats.vp_1d.get("poc", entry + (entry - stop))
    if (target - entry) / max(entry - stop, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("B4", "BUY", round(entry, 2), round(target, 2), round(stop, 2), 2.6, ["B4_VAL_BOUNCE"])


def scan_s3_false_breakout_reverse(
    s1w, s1d, daily, registry, feats, params, permitted,
) -> Signal | None:
    if "SELL" not in permitted or s1w.trend == "UPTREND" or not daily:
        return None
    hit = registry.recent_false_breakout(daily, params["trap_lookback_bars"], params["sr_zone_tolerance_pct"])
    if not hit:
        return None
    idx, trap_bar = hit
    last = daily[-1]
    if last.close >= trap_bar.low:
        return None
    entry = buffer_price(trap_bar.low, params["entry_buffer_pct"], "SELL", "entry")
    stop = trap_bar.high
    target = s1d.nearest_support(entry) or entry - 2 * (stop - entry)
    if (entry - target) / max(stop - entry, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("S3", "SELL", round(entry, 2), round(target, 2), round(stop, 2), 2.8, ["S3_FALSE_BREAKOUT"])


def scan_s4_vah_rejection(s1w, s1d, s4h, daily, feats, params, permitted) -> Signal | None:
    if "SELL" not in permitted or not daily or not feats.vp_1d:
        return None
    if s1w.trend == "UPTREND" or s1d.trend == "UPTREND":
        return None
    vah = feats.vp_1d.get("vah")
    if not vah:
        return None
    last = daily[-1]
    if last.high < vah * 0.995 or last.close >= vah:
        return None
    if vol_ratio(last, feats.avg_vol_20d) < params["value_area_vol_min"]:
        return None
    entry = vah
    stop = buffer_price(vah, params["value_area_stop_buffer_pct"], "SELL", "stop")
    target = feats.vp_1d.get("poc", entry - (stop - entry))
    if (entry - target) / max(stop - entry, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("S4", "SELL", round(entry, 2), round(target, 2), round(stop, 2), 2.6, ["S4_VAH_REJECTION"])


def scan_all_structure(s1w, s1d, s4h, daily, registry, feats, params, permitted) -> list:
    out = []
    for sig in (
        scan_b1_breakout(s1w, s1d, daily, feats, params, permitted),
        scan_b2_breakdown(s1w, s1d, daily, feats, params, permitted),
        scan_b3_false_breakdown_reclaim(s1w, s1d, daily, registry, feats, params, permitted),
        scan_b4_val_bounce(s1w, s1d, s4h, daily, feats, params, permitted),
        scan_s1_support_bounce(s1w, s1d, daily, registry, feats, params, permitted),
        scan_s2_resistance_reject(s1w, s1d, daily, registry, feats, params, permitted),
        scan_s3_false_breakout_reverse(s1w, s1d, daily, registry, feats, params, permitted),
        scan_s4_vah_rejection(s1w, s1d, s4h, daily, feats, params, permitted),
        scan_t1_bull_trap(s1w, s1d, daily, feats, params, permitted),
        scan_t2_bear_trap(s1w, s1d, daily, feats, params, permitted),
    ):
        if sig:
            out.append(sig)
    return out
