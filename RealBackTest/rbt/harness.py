"""
Replay harness — runs the UNMODIFIED live engine (SmartAgent/allstrategy.py)
over real NSE history, bar by bar, with zero lookahead.

Same architecture the synthetic battery (BackTestAgent/backtest.py) proved:
patch the engine's clock and its two data feeds, let everything else — fusion,
Platt calibration, gates, Kelly, bar-walk exits, re-underwriting, learning —
run exactly as it does live. Differences from the synthetic harness:

  * data is REAL: cached Upstox candles for every horizon (native 30-min,
    daily and weekly bars fetched from the API, not resampled approximations)
    and a chain row reconstructed from real expired-option candles.
  * bar availability is modelled: a 30-min bar exists only after it closes,
    a daily bar only the next day, a weekly bar only the next week — the
    exact information set the live engine would have had at that minute.
  * S.datetime is also patched (the synthetic harness skipped this), so
    entry_time/exit_time land in SIM time and walk-forward fold attribution
    of trades is correct.
  * multi-symbol: one brain, one global 1-minute timeline, symbols
    interleaved exactly as live.
"""
import io
import json
import sqlite3
import sys
import types
import contextlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as C
from .chain_replay import IST, SymbolOptionsReplay
from .upstox_data import Cache

sys.path.insert(0, str(C.BASE.parent / "SmartAgent"))
import allstrategy as S                                       # noqa: E402

LOOKBACK_S = {                       # live fetch windows, in seconds
    "short_term": 20 * 86400,
    "swing": 300 * 86400,
    "positional": 750 * 86400,
}


# ---------------------------------------------------------------- SymbolData --
class SymbolData:
    """All cached real bars for one symbol + as-of slicing with availability
    epochs (the no-lookahead core)."""

    def __init__(self, cache, ucfg):
        self.cfg = ucfg
        self.symbol = ucfg["symbol"]
        cols = ["timestamp", "open", "high", "low", "close", "volume", "oi"]

        m1 = cache.equity_df(self.symbol, "minutes", "1")
        if m1.empty:
            raise RuntimeError(f"no 1-min data cached for {self.symbol} — "
                               "run fetch first")
        self.m1 = m1[cols].reset_index(drop=True)
        self.ep1 = m1["ts"].values.astype(np.int64)
        ts = self.m1["timestamp"]
        self.hm = ts.dt.strftime("%H:%M").values
        self.dates = ts.dt.date.values
        self.day_first = {}
        for i, d in enumerate(self.dates):
            self.day_first.setdefault(d, i)

        def prep(unit, iv, avail_shift):
            df = cache.equity_df(self.symbol, unit, iv)
            if df.empty:
                return df, np.array([], dtype=np.int64), \
                    np.array([], dtype=np.int64)
            ep = df["ts"].values.astype(np.int64)
            return df[cols].reset_index(drop=True), ep, ep + avail_shift

        # availability: 30m bar after it closes; daily bar next midnight;
        # weekly bar after the week ends.
        self.m30, self.ep30, self.av30 = prep("minutes", "30", 1800)
        self.d1, self.epd, self.avd = prep("days", "1", 86400)
        self.w1, self.epw, self.avw = prep("weeks", "1", 7 * 86400)

    def slice_h(self, horizon, now_epoch, i_m1=None):
        """Bars the live engine's fetch_bars would return at now_epoch."""
        if horizon == "intraday":
            i = i_m1 if i_m1 is not None else (
                int(np.searchsorted(self.ep1, now_epoch, side="right")) - 1)
            if i < 0:
                return self.m1.iloc[0:0]
            d = self.dates[i]
            return self.m1.iloc[self.day_first[d]:i + 1]
        df, ep, av = {"short_term": (self.m30, self.ep30, self.av30),
                      "swing": (self.d1, self.epd, self.avd),
                      "positional": (self.w1, self.epw, self.avw)}[horizon]
        if df.empty:
            return df
        hi = int(np.searchsorted(av, now_epoch, side="right"))
        lo = int(np.searchsorted(ep, now_epoch - LOOKBACK_S[horizon],
                                 side="left"))
        return df.iloc[lo:hi]


# --------------------------------------------------------------- the harness --
def run_replay(start, end, horizons, symbols=None, cost_bps=C.COST_BPS_DEFAULT,
               flow_ablation=False, tag="main", keep_db=True,
               engine_log=None, progress=print, universe=None,
               cache_path=None):
    """One full pass of the engine over the real window. Returns result dict
    (trades, gates, learning evolution, options diagnostics)."""
    cache = Cache(cache_path or C.CACHE_DB)
    options_ok = cache.get_meta("options_ok", {})
    uni = [u for u in (universe or C.UNIVERSE) if symbols is None
           or u["symbol"] in symbols]

    # ---- per-symbol data + options replay -----------------------------------
    syms, replays, keymap = {}, {}, {}
    for u in uni:
        sd = SymbolData(cache, u)
        syms[u["symbol"]] = sd
        keymap[u["instrument_key"]] = u["symbol"]
        if u["has_options"] and options_ok.get(u["symbol"]):
            rp = SymbolOptionsReplay(cache, u["symbol"],
                                     flow_ablation=flow_ablation)
            if rp.expiries and rp.book:
                replays[u["symbol"]] = rp

    # ---- window clip on the 1-min timeline ----------------------------------
    s_ep = datetime.fromisoformat(start).replace(tzinfo=IST).timestamp()
    e_ep = (datetime.fromisoformat(end).replace(tzinfo=IST)
            + timedelta(days=1)).timestamp()
    events = {}                              # epoch -> [(symbol, i_m1)]
    for sym, sd in syms.items():
        lo = int(np.searchsorted(sd.ep1, s_ep, side="left"))
        hi = int(np.searchsorted(sd.ep1, e_ep, side="left"))
        for i in range(lo, hi):
            events.setdefault(int(sd.ep1[i]), []).append((sym, i))
    timeline = sorted(events)
    if not timeline:
        raise RuntimeError("no 1-min bars in the requested window — check "
                           "the cache / dates")

    # ---- isolated engine state ------------------------------------------------
    C.DB_DIR.mkdir(parents=True, exist_ok=True)
    db_path = C.DB_DIR / f"rbt_{tag}.db"
    db_path.unlink(missing_ok=True)
    S.DB_PATH = str(db_path)
    S.init_database()
    S.COST_BPS_ROUNDTRIP = cost_bps
    S.GATE_STATS.clear()
    for h in S.HORIZON_ENABLED:
        S.HORIZON_ENABLED[h] = h in horizons

    # ---- patch the engine's world ---------------------------------------------
    state = {"ep": float(timeline[0])}
    S.time = types.SimpleNamespace(time=lambda: state["ep"],
                                   sleep=lambda *_: None)

    class SimDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(state["ep"], tz or IST)
    S.datetime = SimDatetime

    spot_idx = {}                            # symbol -> current i_m1

    def fetch_bars_patched(instrument_key, hcfg):
        sym = keymap.get(instrument_key)
        if sym is None:
            return pd.DataFrame()
        hname = {("minutes", "1"): "intraday", ("minutes", "30"):
                 "short_term", ("days", "1"): "swing",
                 ("weeks", "1"): "positional"}[(hcfg["unit"],
                                                hcfg["interval"])]
        return syms[sym].slice_h(hname, state["ep"], spot_idx.get(sym))
    S.fetch_bars = fetch_bars_patched

    chain_memo = {}

    def fetch_chain_patched(instrument_key):
        sym = keymap.get(instrument_key)
        rp = replays.get(sym)
        if rp is None:
            return {}
        mkey = (sym, int(state["ep"]) // 60)
        if mkey in chain_memo:
            return chain_memo[mkey]
        i = spot_idx.get(sym)
        spot = float(syms[sym].m1["close"].iloc[i]) if i is not None else None
        row = rp.chain_row(state["ep"], spot) if spot else {}
        chain_memo.clear()                   # only the current minute matters
        chain_memo[mkey] = row
        return row
    S.fetch_atm_chain_row = fetch_chain_patched

    # ---- run -------------------------------------------------------------------
    brain = S.OmniBrain()
    cfgs = {}
    for u in uni:
        cfgs[u["symbol"]] = {
            "symbol": u["symbol"], "iv_cap": u["iv_cap"],
            "instrument_key": u["instrument_key"],
            "has_options": u["symbol"] in replays}
        brain.symbol_cfg[u["symbol"]] = cfgs[u["symbol"]]
    w0 = {h: dict(brain.learners[h].weights) for h in horizons}

    sink = open(engine_log, "w") if engine_log else io.StringIO()
    n_done, n_total = 0, len(timeline)
    t_wall = datetime.now(timezone.utc)
    with contextlib.redirect_stdout(sink):
        for ep in timeline:
            state["ep"] = float(ep)
            for sym, i in events[ep]:
                spot_idx[sym] = i
                sd = syms[sym]
                tail = sd.m1.iloc[max(0, i - 9):i + 1]
                brain.reconcile_and_manage(sym, tail)
                hm = sd.hm[i]
                for h in horizons:
                    hcfg = S.HORIZONS[h]
                    if hcfg["square_off"] and hm >= hcfg["square_off"]:
                        continue
                    if not brain.due(h, sym):
                        continue
                    brain.evaluate_entry(
                        h, sym, sd.slice_h(h, state["ep"], i), cfgs[sym])
            n_done += 1
            if n_done % 20000 == 0:
                el = (datetime.now(timezone.utc) - t_wall).total_seconds()
                progress(f"  [{tag}] {n_done}/{n_total} minutes "
                         f"({100 * n_done / n_total:.1f}%) "
                         f"{el / 60:.1f} min elapsed, "
                         f"{len(brain.active)} open")
    if engine_log:
        sink.close()

    # ---- mark remaining opens at last close (reported, excluded from R stats)
    open_left = []
    for (h, sym), t in list(brain.active.items()):
        i = spot_idx.get(sym)
        last_px = float(syms[sym].m1["close"].iloc[i])
        open_left.append({"horizon": h, "symbol": sym,
                          "entry_price": t["entry_price"], "qty": t["qty"],
                          "mtm_pnl": (last_px - t["entry_price"]) * t["qty"],
                          "entry_time": t["entry_time"]})

    # ---- collect ----------------------------------------------------------------
    conn = sqlite3.connect(S.DB_PATH)
    trades = pd.read_sql_query(
        "SELECT horizon, symbol, entry_time, exit_time, entry_price, target,"
        " initial_sl, exit_price, qty, outcome, pnl, r_multiple, p_entry,"
        " kelly_f, atr, features_json FROM trades", conn)
    conn.close()
    if not keep_db:
        db_path.unlink(missing_ok=True)

    cost = cost_bps / 1e4 * trades["entry_price"] * trades["qty"] * 2.0
    risk = (trades["entry_price"] - trades["initial_sl"]) * trades["qty"]
    trades["pnl_net"] = trades["pnl"] - cost
    trades["r_net"] = np.where(risk > 1e-9, trades["pnl_net"] / risk, 0.0)

    gates = {}
    for k, v in S.GATE_STATS.items():
        gates[k] = v

    res = {
        "tag": tag, "start": start, "end": end, "horizons": horizons,
        "symbols": [u["symbol"] for u in uni], "cost_bps": cost_bps,
        "flow_ablation": flow_ablation,
        "trades": trades, "open_left": open_left, "gates": gates,
        "dw": {f"{h}.{k}": brain.learners[h].weights[k] - w0[h][k]
               for h in horizons for k in S.FEATURES},
        "credit": {f"{h}.{k}": brain.learners[h].credit[k]
                   for h in horizons for k in S.FEATURES},
        "p_star": {h: brain.learners[h].p_star for h in horizons},
        "calib": {h: {"a": brain.learners[h].calib_a,
                      "b": brain.learners[h].calib_b} for h in horizons},
        "options_diag": {s: r.stats for s, r in replays.items()},
        "db_path": str(db_path),
    }
    return res


def save_run(res, run_dir):
    """Persist run artifacts (trades.csv + summary.json) for the report."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    res["trades"].to_csv(run_dir / "trades.csv", index=False)
    slim = {k: v for k, v in res.items() if k != "trades"}
    slim["n_trades"] = len(res["trades"])
    (run_dir / "summary.json").write_text(
        json.dumps(slim, indent=2, default=str))
    return run_dir
