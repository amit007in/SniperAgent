"""Chronological claim registry with status state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Claim:
    id: str
    layer: str
    claim_type: str
    price: float | None = None
    direction: str | None = None
    first_identified: str = ""
    last_tested: str = ""
    status: str = "active"
    note: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "layer": self.layer,
            "type": self.claim_type,
            "status": self.status,
        }
        if self.price is not None:
            out["price"] = round(self.price, 2)
        if self.direction:
            out["direction"] = self.direction
        if self.first_identified:
            out["first_identified"] = self.first_identified
        if self.last_tested:
            out["last_tested"] = self.last_tested
        if self.note:
            out["note"] = self.note
        if self.meta.get("invalidation_level"):
            out["invalidation_level"] = self.meta["invalidation_level"]
        if self.meta.get("since"):
            out["since"] = self.meta["since"]
        return out


class ClaimRegistry:
    EXPIRE_DAYS = 90

    def __init__(self) -> None:
        self.claims: list[Claim] = []
        self._seq = 0
        self._by_key: dict[tuple, Claim] = {}

    def add(self, **kwargs) -> Claim:
        self._seq += 1
        layer = kwargs.pop("timeframe", kwargs.pop("layer", "1D"))
        claim_type = kwargs.pop("claim_type", kwargs.pop("type", "support")).lower()
        c = Claim(
            id=f"CR{self._seq:03d}",
            layer=layer,
            claim_type=claim_type,
            price=kwargs.get("price"),
            direction=kwargs.get("direction"),
            first_identified=kwargs.get("date", kwargs.get("first_identified", "")),
            last_tested=kwargs.get("last_tested", kwargs.get("date", "")),
            status=kwargs.get("status", "active"),
            note=kwargs.get("note", ""),
            meta=kwargs.get("meta", {}),
        )
        self.claims.append(c)
        if c.price is not None:
            self._by_key[(c.layer, c.claim_type, round(c.price, 2))] = c
        return c

    def to_list(self) -> list[dict]:
        return [c.to_dict() for c in self.claims if c.status != "expired"]

    def find_by_price(self, layer: str, claim_type: str, price: float, tol_pct: float = 0.5) -> Claim | None:
        for c in self.claims:
            if c.layer != layer or c.claim_type != claim_type or c.price is None:
                continue
            if abs(c.price - price) / max(c.price, 1e-9) * 100 <= tol_pct:
                return c
        return None

    @staticmethod
    def _pivot_indices(candles, kind: str, window: int = 2) -> list[tuple[int, float]]:
        out = []
        for i in range(window, len(candles) - window):
            if kind == "high":
                h = candles[i].high
                if all(h >= candles[j].high for j in range(i - window, i + window + 1) if j != i):
                    out.append((i, h))
            else:
                lo = candles[i].low
                if all(lo <= candles[j].low for j in range(i - window, i + window + 1) if j != i):
                    out.append((i, lo))
        return out

    def _update_bar(self, bar, layer: str, tol_pct: float) -> None:
        date = str(bar.date)
        tol = lambda px: px * tol_pct / 100.0

        for c in self.claims:
            if c.layer != layer or c.price is None or c.status not in ("active", "broken_up", "broken_dn"):
                continue
            if c.claim_type == "support":
                if bar.low <= c.price - tol(c.price):
                    if bar.close < c.price:
                        c.status = "broken_dn"
                        c.note = f"Broken below on {date}"
                    c.last_tested = date
                elif abs(bar.low - c.price) <= tol(c.price) or abs(bar.close - c.price) <= tol(c.price):
                    c.last_tested = date
            elif c.claim_type == "resistance":
                if bar.high >= c.price + tol(c.price):
                    if bar.close > c.price:
                        c.status = "broken_up"
                        c.note = f"Broken above on {date}"
                    c.last_tested = date
                elif abs(bar.high - c.price) <= tol(c.price) or abs(bar.close - c.price) <= tol(c.price):
                    c.last_tested = date

    @classmethod
    def build_chronological(
        cls,
        daily,
        weekly,
        h4,
        feats,
        params: dict,
    ) -> "ClaimRegistry":
        """Single oldest→newest pass with live status updates."""
        reg = cls()
        tol = params.get("sr_zone_tolerance_pct", 0.5)

        layers = (("1W", weekly), ("1D", daily), ("4H", h4))
        for layer, candles in layers:
            if not candles:
                continue
            known: set[tuple] = set()
            for i, bar in enumerate(candles):
                reg._update_bar(bar, layer, tol)
                if i >= 2 and i < len(candles) - 2:
                    for kind, ctype, direction in (
                        ("high", "resistance", "down"),
                        ("low", "support", "up"),
                    ):
                        pivots = cls._pivot_indices(candles[: i + 1], kind)
                        if not pivots or pivots[-1][0] != i:
                            continue
                        px = pivots[-1][1]
                        key = (layer, ctype, round(px, 2))
                        if key in known:
                            continue
                        known.add(key)
                        reg.add(
                            layer=layer,
                            claim_type=ctype,
                            price=px,
                            date=str(bar.date),
                            direction=direction,
                            note=f"Pivot {ctype}",
                        )

        if feats.vp_1d.get("poc") and daily:
            reg.add(
                layer="1D",
                claim_type="poc",
                price=feats.vp_1d["poc"],
                date=str(daily[-1].date),
                note="20D POC from volume profile",
            )
            for key, ctype in (("vah", "resistance"), ("val", "support")):
                if feats.vp_1d.get(key):
                    reg.add(
                        layer="1D",
                        claim_type=ctype,
                        price=feats.vp_1d[key],
                        date=str(daily[-1].date),
                        note=f"20D {key.upper()}",
                    )

        if feats.vwap_1d and daily:
            reg.add(
                layer="1D",
                claim_type="vwap",
                price=feats.vwap_1d,
                date=str(daily[-1].date),
                note="Session VWAP",
            )

        if daily and len(daily) >= 2:
            for i in range(1, len(daily)):
                prev, bar = daily[i - 1], daily[i]
                if prev.close <= 0:
                    continue
                gap_pct = abs(bar.open - prev.close) / prev.close * 100
                if gap_pct >= params.get("max_gap_pct", 1.75) * 0.5:
                    reg.add(
                        layer="1D",
                        claim_type="gap_zone",
                        price=bar.open,
                        date=str(bar.date),
                        meta={"gap_pct": round(gap_pct, 2), "fill_target": prev.close},
                        note="Gap zone",
                    )

        reg._merge_near_levels(tol)
        return reg

    @classmethod
    def build(cls, daily, weekly, h4, feats, params) -> "ClaimRegistry":
        return cls.build_chronological(daily, weekly, h4, feats, params)

    def _merge_near_levels(self, tol_pct: float) -> None:
        merged: list[Claim] = []
        for c in sorted(self.claims, key=lambda x: (x.layer, x.claim_type, x.price or 0)):
            if not merged:
                merged.append(c)
                continue
            prev = merged[-1]
            if (
                prev.layer == c.layer
                and prev.claim_type == c.claim_type
                and prev.price is not None
                and c.price is not None
            ):
                if abs(c.price - prev.price) / max(prev.price, 1e-9) * 100 <= tol_pct:
                    prev.price = (prev.price + c.price) / 2
                    prev.last_tested = max(prev.last_tested, c.last_tested)
                    prev.note = f"{prev.note}; merged".strip("; ")
                    continue
            merged.append(c)
        self.claims = merged

    def active_claims(self, layer: str, claim_type: str) -> list[Claim]:
        return [
            c for c in self.claims
            if c.layer == layer and c.claim_type == claim_type and c.status == "active"
        ]

    def resistance_levels(self, layer: str) -> list[float]:
        return [c.price for c in self.active_claims(layer, "resistance") if c.price]

    def support_levels(self, layer: str) -> list[float]:
        return [c.price for c in self.active_claims(layer, "support") if c.price]

    def count_tests(self, level: float, candles, tol_pct: float) -> int:
        tests = 0
        for bar in candles:
            tol = level * tol_pct / 100.0
            if abs(bar.high - level) <= tol or abs(bar.low - level) <= tol:
                tests += 1
        return tests

    def recent_false_breakdown(self, daily, lookback: int, tol_pct: float) -> tuple | None:
        """Return (bar_index, bar) for false breakdown in lookback window."""
        if len(daily) < lookback + 2:
            return None
        for i in range(len(daily) - 2, max(0, len(daily) - lookback - 2), -1):
            bar = daily[i]
            sup = self.support_levels("1D")
            for s in sup:
                tol = s * tol_pct / 100.0
                if bar.low < s - tol and bar.close > s:
                    return i, bar
        return None

    def recent_false_breakout(self, daily, lookback: int, tol_pct: float) -> tuple | None:
        if len(daily) < lookback + 2:
            return None
        for i in range(len(daily) - 2, max(0, len(daily) - lookback - 2), -1):
            bar = daily[i]
            for r in self.resistance_levels("1D"):
                tol = r * tol_pct / 100.0
                if bar.high > r + tol and bar.close < r:
                    return i, bar
        return None
