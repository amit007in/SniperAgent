"""
Fixture dataset for the offline self-test.

Generates a deterministic market into the EXACT cache schema the real fetch
uses (separate DB: Data/RealBackTest/fixture.db) — 1-min/30-min/daily/weekly equity bars
plus an options book whose premiums are priced by Black-Scholes at a KNOWN
IV path. That known path is the ground truth the self-test demands the IV
engine recover, and the whole pipeline (cache -> chain replay -> harness ->
assessment -> report) runs end-to-end on it before any real rupee of data
is trusted.
"""
import os as _os
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import numpy as np

from . import config as C
from .chain_replay import IST
from .iv_engine import bs_price, year_fraction
from .upstox_data import Cache

FIXTURE_DB = Path(_os.environ.get(
    "RBT_FIXTURE_DB",
    str(C.REPO / "Data" / "RealBackTest" / "fixture.db")))
FIXTURE_UCFG = {"symbol": "FIXTURE", "instrument_key": "NSE_EQ|FIXTURE",
                "iv_cap": 75, "has_options": True}
SESSION_MIN = 375                       # 09:15..15:29
N_WARM = 25                             # sessions before the test window
N_TEST = 15                             # sessions the harness replays
S0 = 1400.0
TRUE_IV_BASE = 0.22                     # ground-truth IV path: base + sine
TRUE_IV_AMP = 0.05
STRIKE_STEP = 10.0


def true_iv(epoch, t0):
    """Known IV path (annualised decimal) — the recovery target."""
    days = (epoch - t0) / 86400.0
    return TRUE_IV_BASE + TRUE_IV_AMP * np.sin(2 * np.pi * days / 7.0)


def _sessions(start="2026-03-02", n=N_WARM + N_TEST):
    d = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def build(seed=42, log=print):
    rng = np.random.default_rng(seed)
    FIXTURE_DB.unlink(missing_ok=True)
    cache = Cache(FIXTURE_DB)
    days = _sessions()
    sym = "FIXTURE"

    # ---- 1-min path with planted AR(1) momentum -----------------------------
    n = len(days) * SESSION_MIN
    eps = rng.normal(0, 8e-4, n)
    r = np.empty(n)
    r[0] = eps[0]
    for i in range(1, n):
        r[i] = 0.35 * r[i - 1] + eps[i]          # strong, detectable drift
    close = S0 * np.exp(np.cumsum(r))
    ts = []
    for d in days:
        t0 = datetime.combine(d, dtime(9, 15), tzinfo=IST)
        ts.extend((t0 + timedelta(minutes=k)).isoformat()
                  for k in range(SESSION_MIN))
    spread = np.abs(rng.normal(0, 0.6, n)) + 0.2
    opens = np.r_[close[0], close[:-1]]
    candles = [[ts[i], float(opens[i]),
                float(max(opens[i], close[i]) + spread[i]),
                float(min(opens[i], close[i]) - spread[i]), float(close[i]),
                float(rng.lognormal(10, 0.6)), 0] for i in range(n)]
    cache.put_equity(sym, "minutes", "1", candles)

    # ---- 30-min resample ------------------------------------------------------
    c30 = []
    for s in range(0, n, 30):
        e = min(n, s + 30)
        c30.append([ts[s], float(opens[s]),
                    float(max(c[2] for c in candles[s:e])),
                    float(min(c[3] for c in candles[s:e])),
                    float(close[e - 1]),
                    float(sum(c[5] for c in candles[s:e])), 0])
    cache.put_equity(sym, "minutes", "30", c30)

    # ---- daily: 420 pre-history GBM days + session days -----------------------
    pre_n = 420
    pre_r = rng.normal(2e-4, 0.012, pre_n)
    pre_close = S0 / np.exp(np.sum(pre_r)) * np.exp(np.cumsum(pre_r))
    d0 = days[0]
    pre_dates = []
    d = d0 - timedelta(days=1)
    while len(pre_dates) < pre_n:
        if d.weekday() < 5:
            pre_dates.append(d)
        d -= timedelta(days=1)
    pre_dates.reverse()
    daily = [[datetime.combine(pd_, dtime(0, 0), tzinfo=IST).isoformat(),
              float(pc * 0.998), float(pc * 1.006), float(pc * 0.993),
              float(pc), float(rng.lognormal(13, 0.4)), 0]
             for pd_, pc in zip(pre_dates, pre_close)]
    for k, d_ in enumerate(days):
        s, e = k * SESSION_MIN, (k + 1) * SESSION_MIN
        daily.append([datetime.combine(d_, dtime(0, 0), tzinfo=IST
                                       ).isoformat(),
                      float(opens[s]), float(max(c[2] for c in candles[s:e])),
                      float(min(c[3] for c in candles[s:e])),
                      float(close[e - 1]),
                      float(sum(c[5] for c in candles[s:e])), 0])
    cache.put_equity(sym, "days", "1", daily)

    # ---- weekly: resample dailies ----------------------------------------------
    weekly, wk, key = [], [], None
    for row in daily:
        d_ = datetime.fromisoformat(row[0]).date()
        k = d_.isocalendar()[:2]
        if k != key and wk:
            weekly.append([wk[0][0], wk[0][1],
                           float(max(x[2] for x in wk)),
                           float(min(x[3] for x in wk)), wk[-1][4],
                           float(sum(x[5] for x in wk)), 0])
            wk = []
        key = k
        wk.append(row)
    if wk:
        weekly.append([wk[0][0], wk[0][1], float(max(x[2] for x in wk)),
                       float(min(x[3] for x in wk)), wk[-1][4],
                       float(sum(x[5] for x in wk)), 0])
    cache.put_equity(sym, "weeks", "1", weekly)

    # ---- options book priced at the KNOWN IV path ------------------------------
    expiry = (days[-1] + timedelta(days=7))
    while expiry.weekday() != 3:
        expiry += timedelta(days=1)
    expiry_s = expiry.isoformat()
    cache.conn.execute("REPLACE INTO expiries VALUES (?,?,?)",
                       (sym, expiry_s, "fixture"))
    k_lo = np.floor(close.min() / STRIKE_STEP) * STRIKE_STEP - 2 * STRIKE_STEP
    k_hi = np.ceil(close.max() / STRIKE_STEP) * STRIKE_STEP + 2 * STRIKE_STEP
    strikes = np.arange(k_lo, k_hi + 1, STRIKE_STEP)
    t0 = datetime.combine(days[0], dtime(9, 15), tzinfo=IST).timestamp()
    h, m = C.EXPIRY_CLOSE_HM
    exp_ep = datetime.combine(expiry, dtime(h, m), tzinfo=IST).timestamp()
    epochs = np.array([datetime.fromisoformat(t).timestamp() for t in ts])

    ce_oi = {float(K): 50_000.0 for K in strikes}
    pe_oi = {float(K): 60_000.0 for K in strikes}
    for K in strikes:
        K = float(K)
        for cp in ("CE", "PE"):
            ikey = f"NSE_FO|FX{int(K)}{cp}|{expiry.strftime('%d-%m-%Y')}"
            cache.conn.execute(
                "REPLACE INTO option_contracts VALUES (?,?,?,?,?,?)",
                (ikey, sym, expiry_s, cp, K, 250))
            rows = []
            for i in range(0, n, 1):
                # only print near the money (realistic sparseness elsewhere)
                if abs(close[i] - K) > 4 * STRIKE_STEP and i % 7:
                    continue
                ep = epochs[i]
                T = year_fraction(ep, exp_ep)
                sig = float(true_iv(ep, t0))
                px = bs_price(float(close[i]), K, T, C.RISK_FREE_RATE, sig,
                              cp)
                if px < 0.05:
                    continue
                vol = float(rng.lognormal(7, 0.8))
                oi_book = ce_oi if cp == "CE" else pe_oi
                # OI accumulates from flow correlated with the latent drift
                drift = r[i] * (1 if cp == "CE" else -1)
                oi_book[K] = max(0.0, oi_book[K]
                                 + vol * np.tanh(drift * 400)
                                 + rng.normal(0, vol * 0.1))
                rows.append([ts[i], px, px * 1.002, px * 0.998, float(px),
                             vol, float(oi_book[K])])
            cache.put_option_candles(ikey, rows)
    cache.mark("fixture", n)
    cache.set_meta("options_ok", {sym: True})
    cache.set_meta("window",
                   {"start": days[N_WARM].isoformat(),
                    "end": days[-1].isoformat()})
    cache.set_meta("fixture", {"t0": t0, "expiry_epoch": exp_ep,
                               "true_iv_base": TRUE_IV_BASE,
                               "true_iv_amp": TRUE_IV_AMP})
    log(f"[FIXTURE] built: {n} 1-min bars, {len(strikes)} strikes, "
        f"expiry {expiry_s}, test window "
        f"{days[N_WARM]} → {days[-1]}")
    return {"start": days[N_WARM].isoformat(), "end": days[-1].isoformat(),
            "t0": t0, "expiry_epoch": exp_ep, "n_minutes": n,
            "epochs": epochs, "close": close}
