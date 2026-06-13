"""
Chain reconstructor — rebuilds, per 1-minute bar and with zero lookahead,
the exact dict shape allstrategy.fetch_atm_chain_row() returns live:

    {
      "strike_price": K, "expiry": "YYYY-MM-DD", "underlying_spot_price": S,
      "call_options": {"market_data": {"oi": ..., "bid_qty": ..., "ask_qty": ...},
                        "option_greeks": {"iv": <percent>, "delta": ..., ...}},
      "put_options":  {"market_data": {"oi": ...}}
    }

from cached REAL expired-contract candles:
  oi        -> real, as-of backward join on the contract's 1-min OI series
  iv        -> recovered by BS inversion of the real CE premium (PE parity
               fallback, EWMA-smoothed, jump-filtered) — see iv_engine
  bid/ask   -> BVC executed-flow proxy (v_buy, v_sell) unless ablated
  strike    -> nearest cached strike to the real spot at that minute
  expiry    -> nearest non-past expiry, exactly the live discovery rule

Returns {} when no sufficiently fresh option data exists at that minute —
the engine then runs structure-only with the P_STAR_NO_OPTIONS floor, the
same degradation it applies live on a failed chain fetch.
"""
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import numpy as np

from . import config as C
from .iv_engine import (BVCFlow, IVTracker, bs_greeks, implied_vol,
                        iv_via_parity, year_fraction)

IST = ZoneInfo("Asia/Kolkata")


def _expiry_epoch(expiry_str):
    h, m = C.EXPIRY_CLOSE_HM
    return datetime.fromisoformat(expiry_str).replace(
        hour=h, minute=m, tzinfo=IST).timestamp()


class _Series:
    """Numpy view of one contract's 1-min candles for O(log n) as-of reads."""

    __slots__ = ("ts", "close", "volume", "oi")

    def __init__(self, df):
        self.ts = df["ts"].values.astype(np.int64)
        self.close = df["close"].values.astype(np.float64)
        self.volume = df["volume"].values.astype(np.float64)
        self.oi = df["oi"].values.astype(np.float64)

    def asof(self, epoch):
        """Index of the latest bar with ts <= epoch, or -1."""
        i = int(np.searchsorted(self.ts, epoch, side="right")) - 1
        return i


class SymbolOptionsReplay:
    """All cached option data for one symbol, replayable bar by bar.

    Stateful (IV EWMA, BVC window, PCR continuity) — feed it monotonically
    increasing epochs, which is exactly what the harness does.
    """

    def __init__(self, cache, symbol, flow_ablation=False):
        self.symbol = symbol
        self.flow_ablation = flow_ablation
        cons = cache.contracts(symbol)
        self.expiries = sorted(cons["expiry"].unique())
        self.exp_epochs = np.array([_expiry_epoch(e) for e in self.expiries])
        # strike -> {"CE": _Series, "PE": _Series} per expiry
        self.book = {}
        for _, r in cons.iterrows():
            df = cache.option_series(r["instrument_key"])
            if df.empty:
                continue
            self.book.setdefault(r["expiry"], {}).setdefault(
                float(r["strike"]), {})[r["cp"]] = _Series(df)
        self.strikes = {e: np.array(sorted(d)) for e, d in self.book.items()}
        # smoothers keyed per expiry so a roll restarts cleanly
        self.iv_track = {}
        self.flow = {}
        # diagnostics
        self.n_calls = 0
        self.n_empty = 0
        self.n_stale = 0
        self.iv_raw_fail = 0
        self.iv_parity_used = 0

    # ------------------------------------------------------------------ api --
    def chain_row(self, epoch, spot):
        """The reconstructed ATM row at `epoch` given the real spot. {} when
        options are unavailable/stale at that minute (live-like degrade)."""
        self.n_calls += 1
        j = int(np.searchsorted(self.exp_epochs, epoch, side="left"))
        if j >= len(self.expiries):
            self.n_empty += 1
            return {}
        expiry = self.expiries[j]
        strikes = self.strikes.get(expiry)
        if strikes is None or len(strikes) == 0:
            self.n_empty += 1
            return {}

        # nearest cached strike to the live spot, sliding outward if the
        # closest one lacks fresh prints
        order = np.argsort(np.abs(strikes - spot))
        row = None
        for k_idx in order[:3]:
            K = float(strikes[k_idx])
            pair = self.book[expiry].get(K, {})
            ce, pe = pair.get("CE"), pair.get("PE")
            if ce is None or pe is None:
                continue
            ci, pi = ce.asof(epoch), pe.asof(epoch)
            if ci < 0 or pi < 0:
                continue
            if (epoch - ce.ts[ci] > C.OPT_STALENESS_S
                    or epoch - pe.ts[pi] > C.OPT_STALENESS_S):
                continue
            row = (K, ce, pe, ci, pi)
            break
        if row is None:
            self.n_stale += 1
            return {}
        K, ce, pe, ci, pi = row

        # ----- IV: BS inversion of the real premium -------------------------
        T = year_fraction(epoch, _expiry_epoch(expiry))
        r = C.RISK_FREE_RATE
        raw = implied_vol(ce.close[ci], spot, K, T, r, "CE")
        if raw is None:
            self.iv_raw_fail += 1
            raw = iv_via_parity(pe.close[pi], spot, K, T, r)
            if raw is not None:
                self.iv_parity_used += 1
        tracker = self.iv_track.setdefault(expiry, IVTracker())
        iv_pct = tracker.update(raw * 100.0 if raw is not None else None,
                                epoch=epoch)
        if iv_pct is None:
            return {}                      # no IV estimate yet -> degrade

        # ----- flow: BVC proxy over the ATM CE ------------------------------
        if self.flow_ablation:
            bid_q, ask_q = 0.0, 0.0        # ln((0+1)/(0+1)) = 0 -> x6 = 0
        else:
            fl = self.flow.setdefault(expiry, {}).setdefault(K, BVCFlow())
            # feed any CE bars since the last call for this strike (stateful,
            # monotone epochs); cheap because we remember the cursor
            cur = getattr(fl, "_cursor", 0)
            for b in range(cur, ci + 1):
                fl.update(float(ce.close[b]), float(ce.volume[b]))
            fl._cursor = ci + 1
            bid_q, ask_q = fl._agg()

        greeks = bs_greeks(spot, K, T, r, iv_pct / 100.0, "CE")
        greeks["iv"] = round(float(iv_pct), 2)
        return {
            "strike_price": K,
            "expiry": expiry,
            "underlying_spot_price": float(spot),
            "call_options": {
                "market_data": {"oi": float(ce.oi[ci]),
                                "bid_qty": float(bid_q),
                                "ask_qty": float(ask_q)},
                "option_greeks": greeks,
            },
            "put_options": {
                "market_data": {"oi": float(pe.oi[pi])},
                "option_greeks": {},
            },
        }

    # ----------------------------------------------------------- diagnostics --
    @property
    def stats(self):
        iv_ok = sum(t.n_ok for t in self.iv_track.values())
        iv_fail = sum(t.n_fail for t in self.iv_track.values())
        served = max(1, self.n_calls - self.n_empty - self.n_stale)
        return {
            "calls": self.n_calls,
            "no_contract": self.n_empty,
            "stale": self.n_stale,
            "served": self.n_calls - self.n_empty - self.n_stale,
            "iv_raw_fail": self.iv_raw_fail,
            "iv_parity_rescues": self.iv_parity_used,
            "iv_success_rate": round(iv_ok / max(1, iv_ok + iv_fail), 4),
            "availability": round(
                (self.n_calls - self.n_empty - self.n_stale)
                / max(1, self.n_calls), 4),
            "_served_internal": served,
        }


def session_minutes(d):
    """NSE session [09:15, 15:30) on date d as (start_epoch, end_epoch)."""
    s = datetime.combine(d, dtime(9, 15), tzinfo=IST).timestamp()
    e = datetime.combine(d, dtime(15, 30), tzinfo=IST).timestamp()
    return s, e
