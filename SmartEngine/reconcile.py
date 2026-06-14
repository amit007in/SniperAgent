"""Hard disqualifiers and multi-TF direction cascade."""

from __future__ import annotations

from SmartEngine.regime import TREND_DOWN, TREND_SIDE, TREND_TRANS, TREND_UP
from SmartEngine.state import TimeframeState


def check_hard_disqualifiers(
    s1w: TimeframeState,
    s1d: TimeframeState,
    s4h: TimeframeState,
    feats,
    params: dict,
) -> str | None:
    if s1w.trend == TREND_UP and s1d.trend == TREND_DOWN:
        return "1W/1D timeframe conflict — weekly bullish vs daily bearish"
    if s1w.trend == TREND_DOWN and s1d.trend == TREND_UP:
        return "1W/1D timeframe conflict — weekly bearish vs daily bullish"

    # NOTE: max_gap_pct is NOT a global disqualifier — per the taxonomy it gates
    # M1 only (don't chase a gapped momentum entry). Enforced inside scan_m1/m2.

    poc = feats.vp_1d.get("poc")
    if poc and feats.last_close:
        dz = abs(feats.last_close - poc) / poc * 100
        if dz < params.get("poc_dead_zone_pct", 0.5) and feats.last_bar_vol_ratio < params.get("min_setup_vol", 1.0):
            return "Price in POC dead zone with insufficient participation"

    rs = feats.nifty_rs or {}
    if rs.get("blocked"):
        return rs.get("reason", "Nifty RS filter blocked trade")

    if rs.get("circuit"):
        return "Circuit limit event — no trade"

    avg = feats.avg_vol_20d
    if daily_block := _block_deal_flag(feats, params, avg):
        return daily_block

    return None


def _block_deal_flag(feats, params: dict, avg: float) -> str | None:
    """A block/bulk deal is huge volume WITH ~flat price (negotiated transfer).

    Requires BOTH conditions — high volume alone is normal breakout/momentum
    participation and must NOT be disqualified (M1/B-setups depend on it).
    """
    if avg <= 0:
        return None
    mult = params.get("block_deal_vol_multiple", 3.0)
    max_chg = params.get("block_deal_max_price_chg_pct", 1.0)
    if feats.last_bar_vol_ratio >= mult and feats.last_bar_chg_pct < max_chg:
        return (
            f"Block-deal anomaly: vol {feats.last_bar_vol_ratio:.1f}x with "
            f"flat price ({feats.last_bar_chg_pct:.2f}% < {max_chg}%)"
        )
    return None


def permitted_directions(
    s1w: TimeframeState,
    s1d: TimeframeState,
    s4h: TimeframeState,
) -> set[str]:
    """Explicit cascade truth table — 1W governs, 1D may not oppose it.

    Base cascade (1W → 1D):
        1W UP     + 1D not-down            → {BUY}
        1W DOWN   + 1D not-up              → {SELL}
        1W SIDE   + 1D UP                  → {BUY}
        1W SIDE   + 1D DOWN                → {SELL}
        1W SIDE   + 1D SIDE/TRANS          → {BUY, SELL}   (range — both extremes)
        1W TRANS                           → {} (base) — only the carve-out below trades

    M1/T2 TRANSITIONING carve-out (setup-specific, see smartengine.md §5.4):
        1W TRANS  + 1D UP   + 4H UP        → {BUY}
        1W TRANS  + 1D DOWN + 4H DOWN      → {SELL}
        (requires 1D AND 4H to agree — a TRANS 1W never trades otherwise)

    NOTE: 4H does NOT veto a directional 1W/1D — in an uptrend a 4H pullback is
    the dip you buy, so 4H governs entry timing/setup choice, not direction.
    """
    w, d, h = s1w.trend, s1d.trend, s4h.trend

    if w == TREND_UP:
        base = {"BUY"} if d in (TREND_UP, TREND_SIDE, TREND_TRANS) else set()
    elif w == TREND_DOWN:
        base = {"SELL"} if d in (TREND_DOWN, TREND_SIDE, TREND_TRANS) else set()
    elif w == TREND_SIDE:
        if d == TREND_UP:
            base = {"BUY"}
        elif d == TREND_DOWN:
            base = {"SELL"}
        else:  # 1D SIDE or TRANS → range; allow both extremes
            base = {"BUY", "SELL"}
    else:  # w == TREND_TRANS — only the M1/T2 carve-out may permit a trade
        base = set()
        if d == TREND_UP and h == TREND_UP:
            base.add("BUY")
        elif d == TREND_DOWN and h == TREND_DOWN:
            base.add("SELL")

    return base


def filter_signals_by_direction(signals, permitted: set[str]):
    return [s for s in signals if s.direction in permitted]
