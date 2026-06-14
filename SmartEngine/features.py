"""Feature frame built from multi-timeframe candles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from SmartAgent.quantcore import (
    candles_to_df,
    momentum_tstat,
    variance_ratio,
    volume_zscore,
    wilder_atr,
)


@dataclass
class FeatureFrame:
    symbol: str
    last_close: float
    ma_20d: float
    ma_50d: float
    avg_vol_20d: float
    atr_1d: float
    atr_4h: float
    vol_z_1d: float
    mom_tstat_1d: float
    vr_1d: float
    vwap_1d: float | None = None
    vwap_4h: float | None = None
    vp_1d: dict[str, float] = field(default_factory=dict)
    vp_4h: dict[str, float] = field(default_factory=dict)
    nifty_rs: dict[str, Any] = field(default_factory=dict)
    gap_pct: float = 0.0
    last_bar_vol_ratio: float = 0.0
    last_bar_chg_pct: float = 0.0


def build_features(
    daily,
    weekly,
    h4,
    anchor_metrics: dict,
    *,
    vwap_1d: float | None = None,
    vwap_4h: float | None = None,
    vp_1d: dict | None = None,
    vp_4h: dict | None = None,
    nifty_rs: dict | None = None,
) -> FeatureFrame:
    ddf = candles_to_df(daily)
    hdf = candles_to_df(h4)

    last_close = float(anchor_metrics.get("last_close") or (daily[-1].close if daily else 0.0))
    ma_20d = float(anchor_metrics.get("ma_20d") or 0.0)
    ma_50d = float(anchor_metrics.get("ma_50d") or 0.0)
    avg_vol = float(anchor_metrics.get("avg_vol_20d") or 0.0)

    gap_pct = 0.0
    last_vol_ratio = 0.0
    last_chg_pct = 0.0
    if len(daily) >= 2:
        prev = daily[-2]
        last = daily[-1]
        if prev.close > 0:
            gap_pct = abs(last.open - prev.close) / prev.close * 100.0
            last_chg_pct = abs(last.close - prev.close) / prev.close * 100.0
        if avg_vol > 0:
            last_vol_ratio = last.volume / avg_vol

    return FeatureFrame(
        symbol="",
        last_close=last_close,
        ma_20d=ma_20d,
        ma_50d=ma_50d,
        avg_vol_20d=avg_vol,
        atr_1d=wilder_atr(ddf, 14) if len(ddf) >= 14 else 0.0,
        atr_4h=wilder_atr(hdf, 14) if len(hdf) >= 14 else 0.0,
        vol_z_1d=volume_zscore(ddf),
        mom_tstat_1d=momentum_tstat(ddf),
        vr_1d=variance_ratio(ddf),
        vwap_1d=vwap_1d,
        vwap_4h=vwap_4h,
        vp_1d=vp_1d or {},
        vp_4h=vp_4h or {},
        nifty_rs=nifty_rs or {},
        gap_pct=gap_pct,
        last_bar_vol_ratio=last_vol_ratio,
        last_bar_chg_pct=last_chg_pct,
    )
