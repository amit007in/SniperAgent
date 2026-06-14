"""Momentum setups: M1, M2, V1, V2."""

from __future__ import annotations

from SmartEngine.setups.base import Signal, buffer_price, close_position_in_range, pct_of, vol_ratio


def scan_m1_momentum_long(s1w, s1d, s4h, daily, feats, params, permitted) -> Signal | None:
    if "BUY" not in permitted or not daily:
        return None
    # M1-only gap gate: don't chase a momentum entry that already gapped hard.
    if feats.gap_pct > params["max_gap_pct"]:
        return None
    last = daily[-1]
    if vol_ratio(last, feats.avg_vol_20d) < params["momentum_vol_min"]:
        return None
    rng = last.high - last.low
    if rng <= 0:
        return None
    beyond = (last.close - last.open) / rng * 100 if last.close > last.open else 0
    if beyond < params["momentum_close_beyond_pct"]:
        return None
    if close_position_in_range(last) < 100 - params["momentum_close_range_pct"]:
        return None
    entry = buffer_price(last.close, params["entry_buffer_pct"], "BUY", "entry")
    stop = buffer_price(last.low, params["entry_buffer_pct"], "BUY", "stop")
    move_pct = min(
        max(params["momentum_target_min_pct"], (entry - stop) / entry * 100 * 2),
        params["momentum_target_max_pct"],
    )
    target = entry * (1 + move_pct / 100)
    if (target - entry) / max(entry - stop, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("M1", "BUY", round(entry, 2), round(target, 2), round(stop, 2), 3.2, ["M1_MOMENTUM_LONG"])


def scan_m2_momentum_short(s1w, s1d, s4h, daily, feats, params, permitted) -> Signal | None:
    if "SELL" not in permitted or not daily:
        return None
    # M1/M2-only gap gate: don't chase a momentum entry that already gapped hard.
    if feats.gap_pct > params["max_gap_pct"]:
        return None
    last = daily[-1]
    if vol_ratio(last, feats.avg_vol_20d) < params["momentum_vol_min"]:
        return None
    rng = last.high - last.low
    if rng <= 0 or last.close >= last.open:
        return None
    beyond = (last.open - last.close) / rng * 100
    if beyond < params["momentum_close_beyond_pct"]:
        return None
    if close_position_in_range(last) > params["momentum_close_range_pct"]:
        return None
    entry = buffer_price(last.close, params["entry_buffer_pct"], "SELL", "entry")
    stop = buffer_price(last.high, params["entry_buffer_pct"], "SELL", "stop")
    move_pct = min(
        max(params["momentum_target_min_pct"], (stop - entry) / entry * 100 * 2),
        params["momentum_target_max_pct"],
    )
    target = entry * (1 - move_pct / 100)
    if (entry - target) / max(stop - entry, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("M2", "SELL", round(entry, 2), round(target, 2), round(stop, 2), 3.2, ["M2_MOMENTUM_SHORT"])


def _in_value_area(feats, price: float, params) -> bool:
    val, vah = feats.vp_1d.get("val"), feats.vp_1d.get("vah")
    if not val or not vah:
        return False
    return val <= price <= vah


def scan_v1_value_area_long(s1d, daily, feats, params, permitted) -> Signal | None:
    if "BUY" not in permitted or not daily or not feats.vp_1d:
        return None
    last = daily[-1]
    val = feats.vp_1d.get("val", 0)
    if not _in_value_area(feats, last.close, params):
        return None
    if last.close < val:
        return None
    if vol_ratio(last, feats.avg_vol_20d) < params["value_area_vol_min"]:
        return None
    poc = feats.vp_1d.get("poc", val)
    if abs(last.close - poc) / max(poc, 1e-9) * 100 < params["poc_dead_zone_pct"]:
        return None
    entry = buffer_price(last.close, params["entry_buffer_pct"], "BUY", "entry")
    stop = buffer_price(val, params["value_area_stop_buffer_pct"], "BUY", "stop")
    target = feats.vp_1d.get("vah", entry + (entry - stop))
    if (target - entry) / max(entry - stop, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("V1", "BUY", round(entry, 2), round(target, 2), round(stop, 2), 2.4, ["V1_VALUE_AREA_LONG"])


def scan_v2_value_area_short(s1d, daily, feats, params, permitted) -> Signal | None:
    if "SELL" not in permitted or not daily or not feats.vp_1d:
        return None
    last = daily[-1]
    vah = feats.vp_1d.get("vah", 0)
    if not _in_value_area(feats, last.close, params):
        return None
    if last.close > vah:
        return None
    if vol_ratio(last, feats.avg_vol_20d) < params["value_area_vol_min"]:
        return None
    poc = feats.vp_1d.get("poc", vah)
    if abs(last.close - poc) / max(poc, 1e-9) * 100 < params["poc_dead_zone_pct"]:
        return None
    entry = buffer_price(last.close, params["entry_buffer_pct"], "SELL", "entry")
    stop = buffer_price(vah, params["value_area_stop_buffer_pct"], "SELL", "stop")
    target = feats.vp_1d.get("val", entry - (stop - entry))
    if (entry - target) / max(stop - entry, 1e-9) < params["min_rr_ratio"]:
        return None
    return Signal("V2", "SELL", round(entry, 2), round(target, 2), round(stop, 2), 2.4, ["V2_VALUE_AREA_SHORT"])


def scan_all_momentum(s1w, s1d, s4h, daily, feats, params, permitted) -> list:
    out = []
    for fn in (scan_m1_momentum_long, scan_m2_momentum_short, scan_v1_value_area_long, scan_v2_value_area_short):
        if fn in (scan_m1_momentum_long, scan_m2_momentum_short):
            sig = fn(s1w, s1d, s4h, daily, feats, params, permitted)
        else:
            sig = fn(s1d, daily, feats, params, permitted)
        if sig:
            out.append(sig)
    return out
