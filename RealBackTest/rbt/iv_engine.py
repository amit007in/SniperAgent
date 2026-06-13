"""
IV engine — recover what the historical API does not provide.

Upstox expired-instrument candles carry OHLCV + OI but NO greeks, NO IV and
NO depth. This module rebuilds the missing pieces from first principles:

  * implied volatility: Black-Scholes inversion (Brent) of the real option
    premium against the real spot, strike and time-to-expiry. ATM short-dated
    equity options are the best-conditioned case for inversion (vega is
    maximal at the money), which is exactly the row the engine consumes.
  * greeks: analytic BS partials evaluated at the recovered IV.
  * order-flow imbalance: Bulk Volume Classification (Easley, Lopez de
    Prado, O'Hara 2012) — split each option bar's volume into buy/sell using
    the normal CDF of the standardised price change. This is the honest,
    literature-grounded proxy for the live bid/ask-quantity imbalance, and
    the harness A/Bs it against a zeroed flow plane so the report can prove
    how much of the measured edge rests on the proxy.

American-exercise premium on non-dividend single stocks is economically
European for calls (no early-exercise value) and near-European for ATM
short-dated puts, so BS inversion is the right tool at the row we need.
"""
import math

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from . import config as C


# ---------------------------------------------------------------- BS pricing --
def bs_price(S, K, T, r, sigma, cp="CE"):
    """Black-Scholes price. T in years, sigma annualised decimal."""
    if T <= 0 or sigma <= 0:
        intrinsic = max(S - K, 0.0) if cp == "CE" else max(K - S, 0.0)
        return intrinsic
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    if cp == "CE":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(S, K, T, r, sigma, cp="CE"):
    """Analytic BS greeks at the recovered IV (annualised decimal sigma)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    pdf = norm.pdf(d1)
    delta = norm.cdf(d1) if cp == "CE" else norm.cdf(d1) - 1.0
    gamma = pdf / (S * sigma * sqT)
    vega = S * pdf * sqT / 100.0                       # per 1 vol-pt
    if cp == "CE":
        theta = (-S * pdf * sigma / (2 * sqT)
                 - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365.0
    else:
        theta = (-S * pdf * sigma / (2 * sqT)
                 + r * K * math.exp(-r * T) * norm.cdf(-d2)) / 365.0
    return {"delta": round(delta, 4), "gamma": round(gamma, 6),
            "theta": round(theta, 4), "vega": round(vega, 4)}


# ------------------------------------------------------------- IV inversion --
def implied_vol(price, S, K, T, r, cp="CE"):
    """
    Invert BS for sigma. Returns annualised decimal, or None when the premium
    sits outside no-arbitrage bounds (stale print, sub-intrinsic close) or
    the root is out of [0.5%, 500%].
    """
    if price is None or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    intrinsic = max(S - K, 0.0) if cp == "CE" else max(K - S, 0.0)
    upper = S if cp == "CE" else K * math.exp(-r * T)
    if price <= intrinsic + 1e-9 or price >= upper - 1e-9:
        return None

    def f(sig):
        return bs_price(S, K, T, r, sig, cp) - price

    lo, hi = 0.005, 5.0
    try:
        flo, fhi = f(lo), f(hi)
        if flo * fhi > 0:
            return None
        return float(brentq(f, lo, hi, xtol=1e-8, maxiter=100))
    except Exception:
        return None


def iv_via_parity(pe_price, S, K, T, r):
    """CE premium implied by put-call parity, then inverted. Lets a liquid PE
    print rescue an illiquid/stale CE print at the same strike."""
    if pe_price is None or pe_price <= 0:
        return None
    ce_synth = pe_price + S - K * math.exp(-r * T)
    return implied_vol(ce_synth, S, K, T, r, "CE")


class IVTracker:
    """
    Per-symbol smoother for the recovered IV stream.

    Raw per-minute inversions are noisy (stale closes, tick bounce). We keep
    an EWMA and reject single-bar jumps beyond IV_JUMP_CAP_PTS unless the
    move persists (3 consecutive rejections re-anchor the EWMA — a real vol
    event must be followed, not suppressed). When inversion fails outright,
    the last smoothed value carries forward and the failure is counted.

    The smoothing is TIME-AWARE: lambda is per-minute, so the effective
    weight of the old estimate decays with elapsed time — a 30-minute or
    overnight gap means the fresh inversion dominates instead of being
    dragged toward a stale level. The jump filter likewise only applies to
    near-adjacent observations (a big move across a long gap is news, not
    noise).
    """

    def __init__(self, lam=C.IV_EWMA_LAMBDA, jump_cap=C.IV_JUMP_CAP_PTS):
        self.lam, self.jump_cap = lam, jump_cap
        self.ewma = None
        self.last_epoch = None
        self.reject_streak = 0
        self.n_ok = 0
        self.n_fail = 0
        self.n_reject = 0

    def update(self, raw_iv_pct, epoch=None):
        """Feed a raw recovered IV in PERCENT (or None) observed at `epoch`.
        Returns smoothed percent or None when no estimate exists yet."""
        if raw_iv_pct is None or not (C.IV_MIN_PCT <= raw_iv_pct
                                      <= C.IV_MAX_PCT):
            self.n_fail += 1
            return self.ewma
        dt_min = (1.0 if epoch is None or self.last_epoch is None
                  else max(1.0, (epoch - self.last_epoch) / 60.0))
        self.last_epoch = epoch
        if self.ewma is None:
            self.ewma = raw_iv_pct
            self.n_ok += 1
            return self.ewma
        if (abs(raw_iv_pct - self.ewma) > self.jump_cap
                and dt_min <= 5.0):            # adjacent-bar spike only
            self.reject_streak += 1
            self.n_reject += 1
            if self.reject_streak >= 3:        # persistent => real vol event
                self.ewma = raw_iv_pct
                self.reject_streak = 0
            return self.ewma
        self.reject_streak = 0
        self.n_ok += 1
        lam_eff = self.lam ** dt_min           # per-minute decay
        self.ewma = lam_eff * self.ewma + (1.0 - lam_eff) * raw_iv_pct
        return self.ewma

    @property
    def stats(self):
        tot = max(1, self.n_ok + self.n_fail)
        return {"ok": self.n_ok, "fail": self.n_fail,
                "reject": self.n_reject, "success_rate": self.n_ok / tot}


# ------------------------------------------------------------ BVC flow proxy --
class BVCFlow:
    """
    Bulk Volume Classification over a trailing window of ATM-CE bars.

    buy_frac(bar) = Phi( dClose / sigma_ewma(|dClose|) )
    V_buy = sum(vol * buy_frac), V_sell = sum(vol * (1 - buy_frac))

    The harness presents (V_buy, V_sell) as (bid_qty, ask_qty) so the
    engine's x6 = ln((bid+1)/(ask+1)) becomes the signed executed-flow
    imbalance — same log-ratio scale, same sign convention (bullish > 0).
    """

    def __init__(self, lam=C.FLOW_SIGMA_LAMBDA, window=C.FLOW_WINDOW_BARS):
        self.lam, self.window = lam, window
        self.sig = None                       # EWMA of |dClose|
        self.prev_close = None
        self.buf = []                         # [(v_buy, v_sell), ...]

    def update(self, close, volume):
        """Feed one option bar (chronological). Returns (v_buy, v_sell)
        aggregated over the trailing window."""
        if self.prev_close is None or volume is None or volume <= 0:
            self.prev_close = close
            return self._agg()
        dc = close - self.prev_close
        self.prev_close = close
        a = abs(dc)
        self.sig = a if self.sig is None else (self.lam * self.sig
                                               + (1 - self.lam) * a)
        z = dc / self.sig if self.sig and self.sig > 1e-12 else 0.0
        buy_frac = float(norm.cdf(z))
        self.buf.append((volume * buy_frac, volume * (1.0 - buy_frac)))
        if len(self.buf) > self.window:
            self.buf.pop(0)
        return self._agg()

    def _agg(self):
        if not self.buf:
            return 0.0, 0.0
        b = sum(x for x, _ in self.buf)
        s = sum(y for _, y in self.buf)
        return b, s


def year_fraction(now_epoch, expiry_epoch):
    """Calendar-time year fraction to expiry settlement."""
    return max(0.0, (expiry_epoch - now_epoch) / (365.0 * 86400.0))
