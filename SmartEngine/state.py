"""Per-timeframe structural state extraction with regime confidence."""

from __future__ import annotations

from dataclasses import dataclass, field

from SmartEngine.regime import RegimeResult, TREND_SIDE, resolve_regime
from SmartEngine.registry import ClaimRegistry


@dataclass
class TimeframeState:
    layer: str
    trend: str
    last_close: float
    trend_confidence: float = 0.5
    phase: str = "CONSOLIDATION"
    vol_character: str = "NEUTRAL"
    vwap_position: str = "AT"
    vp_position: str = "INSIDE_VA"
    ma20_slope: float = 0.0
    ambiguous: bool = False
    resistance: list[float] = field(default_factory=list)
    support: list[float] = field(default_factory=list)
    higher_highs: int = 0
    higher_lows: int = 0
    lower_highs: int = 0
    lower_lows: int = 0
    period_high: float = 0.0
    period_low: float = 0.0
    regime: RegimeResult | None = None

    @property
    def timeframe(self) -> str:
        return self.layer

    def nearest_resistance(self, price: float) -> float | None:
        above = [r for r in self.resistance if r > price]
        return min(above) if above else None

    def nearest_support(self, price: float) -> float | None:
        below = [s for s in self.support if s < price]
        return max(below) if below else None


def _pivot_sequence(candles, kind: str, window: int = 2) -> list[float]:
    seq = []
    for i in range(window, len(candles) - window):
        if kind == "high":
            h = candles[i].high
            if all(h >= candles[j].high for j in range(i - window, i + window + 1) if j != i):
                seq.append(h)
        else:
            lo = candles[i].low
            if all(lo <= candles[j].low for j in range(i - window, i + window + 1) if j != i):
                seq.append(lo)
    return seq


def _vwap_position(close: float, vwap: float | None) -> str:
    if not vwap or vwap <= 0:
        return "AT"
    pct = (close - vwap) / vwap * 100
    if pct > 0.25:
        return "ABOVE"
    if pct < -0.25:
        return "BELOW"
    return "AT"


def _vp_position(close: float, vp: dict) -> str:
    poc, vah, val = vp.get("poc"), vp.get("vah"), vp.get("val")
    if not poc:
        return "INSIDE_VA"
    if val and vah and val <= close <= vah:
        if abs(close - poc) / poc * 100 < 0.3:
            return "AT_POC"
        return "INSIDE_VA"
    if vah and close > vah:
        return "ABOVE_VAH"
    if val and close < val:
        return "BELOW_VAL"
    return "INSIDE_VA"


def extract_state(
    candles,
    layer: str,
    registry: ClaimRegistry,
    *,
    feats=None,
    ma_20: float = 0.0,
    smart_params: dict | None = None,
    lookback: int = 60,
) -> TimeframeState:
    if not candles:
        return TimeframeState(layer=layer, trend=TREND_SIDE, last_close=0.0)

    window = candles[-lookback:] if len(candles) > lookback else candles
    highs = _pivot_sequence(window, "high")
    lows = _pivot_sequence(window, "low")

    hh = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
    lh = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    hl = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    ll = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i - 1])

    regime = resolve_regime(window, (hh, hl, lh, ll), ma_20=ma_20, smart_params=smart_params)

    ma_slope = 0.0
    if len(window) >= 6 and ma_20 > 0:
        ma_slope = (window[-1].close - window[-6].close) / window[-6].close * 100

    vp = feats.vp_1d if feats and layer == "1D" else (feats.vp_4h if feats else {})
    vwap = feats.vwap_1d if feats and layer in ("1D", "1W") else (feats.vwap_4h if feats else None)

    return TimeframeState(
        layer=layer,
        trend=regime.trend,
        last_close=window[-1].close,
        trend_confidence=regime.confidence,
        phase=regime.phase,
        vol_character=regime.vol_character,
        vwap_position=_vwap_position(window[-1].close, vwap),
        vp_position=_vp_position(window[-1].close, vp or {}),
        ma20_slope=ma_slope,
        ambiguous=regime.ambiguous,
        resistance=registry.resistance_levels(layer),
        support=registry.support_levels(layer),
        higher_highs=hh,
        higher_lows=hl,
        lower_highs=lh,
        lower_lows=ll,
        period_high=max(c.high for c in window),
        period_low=min(c.low for c in window),
        regime=regime,
    )
