"""
Benchmarks + statistical confidence machinery.

The strategy's mean net R is meaningless in isolation. We anchor it three
ways, all on the SAME real bars and the SAME exit geometry:

  1. Random-entry null — entries drawn uniformly from the eligible
     evaluation grid, exits via the identical ATR triple-barrier walk
     (barriers, chandelier trail, time stop, intraday square-off), identical
     costs. If evidence-gated entries don't beat the high percentiles of
     this luck distribution, the evidence plane adds nothing.
  2. Stationary block bootstrap on the strategy's net-R sequence — p-value
     for mean R > 0 that respects serial dependence between trades.
  3. Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014) — discounts the
     Sharpe for non-normality of R and for the number of strategy variants
     effectively tried.

Plus buy-and-hold per symbol for an absolute reference.
"""
import numpy as np
from scipy.stats import norm

from . import config as C
from .chain_replay import IST


# ------------------------------------------------------ exit-geometry replica --
def simulate_exit(sd, i_entry, hcfg, atr, cost_bps):
    """Replay the engine's bar-walk exit for a hypothetical entry at the
    close of m1 bar i_entry. Mirrors allstrategy._walk_bar pessimistic
    ordering (square-off / time at the open, stop before target, gap fills
    at the open) with a plain time stop (no re-underwriting extensions —
    the null should not enjoy the strategy's smartest exit either).
    Returns net R or None if the position never resolves in data."""
    m1 = sd.m1
    entry = float(m1["close"].iloc[i_entry])
    entry_ep = float(sd.ep1[i_entry])
    entry_date = sd.dates[i_entry]
    rt, rs = hcfg["rt"], hcfg["rs"]
    target = entry + rt * atr
    sl = entry - rs * atr
    initial_sl = sl
    hwm = entry
    trail_armed = False
    arm_level = entry + hcfg["arm_atr"] * atr
    trail_dist = hcfg["trail_atr"] * atr
    unit, n = hcfg["max_hold"]
    limit = n * 60 if unit == "min" else n * 86400
    sq = hcfg["square_off"]
    risk = entry - initial_sl
    if risk <= 1e-9:
        return None
    cost_r = (2.0 * cost_bps / 1e4) * entry / risk

    o_arr = m1["open"].values
    h_arr = m1["high"].values
    l_arr = m1["low"].values
    for i in range(i_entry + 1, len(m1)):
        ep = float(sd.ep1[i])
        o, h, l = float(o_arr[i]), float(h_arr[i]), float(l_arr[i])
        if sq and (sd.dates[i] > entry_date or sd.hm[i] >= sq):
            return (o - entry) / risk - cost_r
        if ep - entry_ep >= limit:
            return (o - entry) / risk - cost_r
        if l <= sl:
            fill = min(sl, o)
            return (fill - entry) / risk - cost_r
        if h >= target:
            fill = max(target, o)
            return (fill - entry) / risk - cost_r
        if h > hwm:
            hwm = h
        if not trail_armed and h >= arm_level:
            trail_armed = True
        if trail_armed:
            new_sl = hwm - trail_dist
            if new_sl > sl:
                sl = new_sl
    return None                              # ran off the end of data


def eligible_entries(sd, S, horizon, start_ep, end_ep):
    """1-min indices where the engine could have evaluated this horizon:
    in-window, in-session, enough native bars, before square-off."""
    hcfg = S.HORIZONS[horizon]
    step = max(1, hcfg["eval_every_min"])
    out = []
    lo = int(np.searchsorted(sd.ep1, start_ep, side="left"))
    hi = int(np.searchsorted(sd.ep1, end_ep, side="left"))
    for i in range(lo, hi, step):
        if hcfg["square_off"] and sd.hm[i] >= hcfg["square_off"]:
            continue
        df = sd.slice_h(horizon, float(sd.ep1[i]), i)
        if len(df) >= hcfg["min_bars"]:
            out.append(i)
    return out


def random_entry_null(sd, S, horizon, n_trades, start_ep, end_ep,
                      cost_bps, n_draws=200, seed=7):
    """Distribution of mean net R from n_draws random-entry portfolios of
    n_trades each. Returns array of draw means (may be empty)."""
    cand = eligible_entries(sd, S, horizon, start_ep, end_ep)
    if not cand or n_trades < 1:
        return np.array([])
    rng = np.random.default_rng(seed)
    hcfg = S.HORIZONS[horizon]
    means = []
    for _ in range(n_draws):
        picks = rng.choice(cand, size=min(n_trades, len(cand)),
                           replace=False)
        rs = []
        for i in picks:
            df = sd.slice_h(horizon, float(sd.ep1[i]), int(i))
            atr = S.wilder_atr(df, hcfg["atr_period"])
            if atr is None or not np.isfinite(atr) or atr <= 0:
                continue
            r = simulate_exit(sd, int(i), hcfg, float(atr), cost_bps)
            if r is not None:
                rs.append(r)
        if rs:
            means.append(float(np.mean(rs)))
    return np.array(means)


# ----------------------------------------------------------------- statistics --
def block_bootstrap_p(r, n_boot=10000, mean_block=5, seed=11):
    """Stationary bootstrap (Politis-Romano) one-sided p-value for
    H1: mean(R) > 0. p = fraction of resampled means <= 0."""
    r = np.asarray(r, dtype=float)
    n = len(r)
    if n < 5:
        return None
    rng = np.random.default_rng(seed)
    p_geo = 1.0 / mean_block
    means = np.empty(n_boot)
    for b in range(n_boot):
        idx = np.empty(n, dtype=np.int64)
        i = rng.integers(0, n)
        for t in range(n):
            idx[t] = i
            if rng.random() < p_geo:
                i = rng.integers(0, n)
            else:
                i = (i + 1) % n
        means[b] = r[idx].mean()
    return float(np.mean(means <= 0.0))


def deflated_sharpe(r, n_trials=10):
    """DSR per Bailey & Lopez de Prado (2014) on the per-trade R series.
    Returns (sr, dsr_prob) — dsr_prob is P(true SR > 0 | non-normality,
    multiple testing)."""
    r = np.asarray(r, dtype=float)
    n = len(r)
    if n < 10 or np.std(r) < 1e-12:
        return None, None
    sr = float(np.mean(r) / np.std(r, ddof=1))
    g3 = float(((r - r.mean()) ** 3).mean() / np.std(r) ** 3)
    g4 = float(((r - r.mean()) ** 4).mean() / np.std(r) ** 4)
    em = 0.5772156649
    z1 = norm.ppf(1 - 1.0 / n_trials) if n_trials > 1 else 0.0
    z2 = norm.ppf(1 - 1.0 / (n_trials * np.e)) if n_trials > 1 else 0.0
    sr0 = np.sqrt(1.0 / max(1, n - 1)) * ((1 - em) * z1 + em * z2)
    denom = np.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4.0 * sr ** 2))
    dsr = float(norm.cdf((sr - sr0) * np.sqrt(n - 1) / denom))
    return sr, dsr


def buy_and_hold(sd, start_ep, end_ep):
    """Absolute reference on the symbol's daily closes."""
    d1, epd = sd.d1, sd.epd
    if d1.empty:
        return None
    lo = int(np.searchsorted(epd, start_ep, side="left"))
    hi = int(np.searchsorted(epd, end_ep, side="right"))
    px = d1["close"].values[lo:hi]
    if len(px) < 20:
        return None
    ret = px[-1] / px[0] - 1.0
    lr = np.diff(np.log(px))
    vol = float(np.std(lr) * np.sqrt(252))
    sharpe = float((np.mean(lr) * 252 - C.RISK_FREE_RATE)
                   / max(1e-9, vol))
    peak = np.maximum.accumulate(px)
    mdd = float(np.max(1.0 - px / peak))
    return {"total_return": float(ret), "ann_vol": vol,
            "sharpe": sharpe, "max_dd_pct": mdd,
            "sessions": int(len(px))}


def equity_curve_stats(trades):
    """Strategy-side aggregates on net pnl, in entry order."""
    if trades.empty:
        return {"total_pnl_net": 0.0, "max_dd_rupees": 0.0}
    t = trades.sort_values("entry_time")
    cum = t["pnl_net"].cumsum().values
    peak = np.maximum.accumulate(cum)
    return {"total_pnl_net": float(cum[-1]),
            "max_dd_rupees": float(np.max(peak - cum))}
