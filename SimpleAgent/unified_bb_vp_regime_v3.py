"""
UNIFIED v3: Double Bollinger (daily) + Volume Profile (intraday) + Regime split + Backtest.
Wired to the SniperAgent marketdata.db (equity_candles).

DB SCHEMA IT EXPECTS  (table: equity_candles)
---------------------------------------------
  symbol TEXT, unit TEXT, interval TEXT, ts INTEGER (epoch SECONDS, UTC),
  open REAL, high REAL, low REAL, close REAL, volume REAL, oi REAL
Timeframe is selected by (unit, interval); default 'minutes' / '30'.
ts is UTC epoch seconds -> converted to IST (Asia/Kolkata) so DAILY resample
buckets align to the NSE session (09:15-15:30 IST).

REALISTIC FILLS (from v2, kept)
-------------------------------
  - True-range ATR (gap-aware stop distance)
  - Next-open entry (no same-close lookahead)
  - Gap-aware exit: long stop fills at min(open, trail)
  - Per-side slippage + round-trip costs (bps), NET vs GROSS reported

SYMBOL / DATE SELECTION  (all case-insensitive, all optional)
-------------------------------------------------------------
  --symbol RELIANCE              one symbol
  --symbols reliance,infy,tcs    group (comma-separated, any case)
  (omit both)                    ALL symbols in the table
  --start 2023-01-01             inclusive lower date bound (optional)
  --end   2024-12-31             inclusive upper date bound (optional)
  (omit both)                    entire history in the db

Each symbol is processed independently; trades are POOLED across symbols for the
regime report (more trades per cell = less noise). Per-symbol signal counts are
printed too.

Requirements: pip install pandas numpy sqlalchemy
Usage:
    python unified_bb_vp_regime_v3.py                                  # all symbols, all dates
    python unified_bb_vp_regime_v3.py --symbol reliance
    python unified_bb_vp_regime_v3.py --symbols reliance,infy --start 2023-01-01 --end 2024-12-31
    python unified_bb_vp_regime_v3.py --interval 1 --cost-bps 35 --slip-bps 5
    python unified_bb_vp_regime_v3.py --csv reliance_30m.csv          # CSV still supported
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
from shared_data import market_data_db  # noqa: E402

DEFAULT_DB = str(market_data_db())
IST = "Asia/Kolkata"

PARAMS = {
    "inner_period": 20, "inner_sd": 2.0,
    "outer_period": 20, "outer_sd": 2.5,
    "bw_lookback": 100, "trend_pct": 0.7, "range_pct": 0.3,
    "vp_window_days": 50, "vp_bins": 24, "hvn_ratio": 1.3, "lvn_ratio": 0.6,
    "use_lvn": False,          # LVN-confirmed breakout variant off (HVN-only focus)
    "use_raw_rev": False,      # reversion: take HVN-confirmed ONLY; skip mid/LVN reversions
    "min_profile_bars": 50, "include_signal_day": True,
    "atr_period": 14, "atr_mult": 2.0,
    "exit_mode": "target",     # "target" = exit at mean (mid-band); "trail" = ATR trailing stop
    "stop_mult": 1.0,          # hard stop = stop_mult x ATR below entry (reversion target mode)
    # --- breakout fixed-% exit (no ATR) ---
    "brk_target_pct": 0.02,    # exit all at +2%
    "brk_stop_pct": 0.02,      # initial hard stop -2%
    "brk_be_trigger_pct": 0.01,# move stop to breakeven once +1% is touched
    "entry_next_open": True,
    "slip_bps": 5.0,
    "cost_bps": 35.0,
}

COLUMN_MAP = {"timestamp": "timestamp", "open": "open", "high": "high",
              "low": "low", "close": "close", "volume": "volume"}
NEED = ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------- data
def _to_ist_index(df, ts_col):
    """epoch-seconds (UTC) -> tz-naive IST DatetimeIndex named 'timestamp'."""
    t = pd.to_datetime(df[ts_col], unit="s", utc=True).dt.tz_convert(IST).dt.tz_localize(None)
    df = df.drop(columns=[ts_col]).copy()
    df["timestamp"] = t
    return df


def list_symbols(db, unit, interval):
    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT DISTINCT symbol FROM equity_candles WHERE unit=? AND interval=? ORDER BY symbol",
            (unit, interval)).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def load_db_symbol(db, symbol, unit, interval, start, end):
    """Load one symbol's intraday OHLCV from equity_candles -> IST-indexed df."""
    con = sqlite3.connect(db)
    q = ("SELECT ts, open, high, low, close, volume FROM equity_candles "
         "WHERE UPPER(symbol)=UPPER(?) AND unit=? AND interval=?")
    p = [symbol, unit, interval]
    if start:
        q += " AND ts >= ?"; p.append(int(pd.Timestamp(start, tz=IST).timestamp()))
    if end:
        # inclusive end-of-day
        q += " AND ts <= ?"
        p.append(int((pd.Timestamp(end, tz=IST) + pd.Timedelta(days=1)).timestamp()) - 1)
    q += " ORDER BY ts"
    try:
        df = pd.read_sql_query(q, con, params=p)
    finally:
        con.close()
    if df.empty:
        return df
    df = _to_ist_index(df, "ts")
    df = df.sort_values("timestamp").set_index("timestamp")[NEED].dropna()
    return df


def load_csv(path):
    df = pd.read_csv(path)
    df = df.rename(columns={v: k for k, v in COLUMN_MAP.items() if v in df.columns})
    miss = [c for c in NEED if c not in df.columns]
    if miss:
        raise SystemExit(f"Missing {miss}. Edit COLUMN_MAP. Found {list(df.columns)}")
    if "timestamp" not in df.columns:
        raise SystemExit("Need a 'timestamp' column to resample to daily.")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").set_index("timestamp")[NEED].dropna()


# ---------------------------------------------------------------- indicators
def bb(s, p, sd):
    ma = s.rolling(p).mean(); std = s.rolling(p).std()
    return ma, ma + sd * std, ma - sd * std


def true_range(daily):
    H, L, C = daily["high"], daily["low"], daily["close"]
    prevC = C.shift(1)
    return pd.concat([(H - L).abs(), (H - prevC).abs(), (L - prevC).abs()], axis=1).max(axis=1)


def vp_node(intraday, day, price, p):
    start = day - pd.Timedelta(days=p["vp_window_days"])
    end = (day + pd.Timedelta(hours=23, minutes=59, seconds=59)
           if p["include_signal_day"] else day - pd.Timedelta(seconds=1))
    seg = intraday[(intraday.index > start) & (intraday.index <= end)]
    if len(seg) < p["min_profile_bars"]:
        return None
    pmin, pmax = seg["low"].min(), seg["high"].max()
    if pmax <= pmin:
        return None
    edges = np.linspace(pmin, pmax, p["vp_bins"] + 1)
    typ = (seg["high"] + seg["low"] + seg["close"]) / 3.0
    hist, _ = np.histogram(typ, bins=edges, weights=seg["volume"])
    if not (hist > 0).any():
        return None
    b = int(np.clip(np.digitize(price, edges) - 1, 0, p["vp_bins"] - 1))
    med = np.median(hist[hist > 0])
    if med == 0:
        return None
    r = hist[b] / med
    return "HVN" if r >= p["hvn_ratio"] else "LVN" if r <= p["lvn_ratio"] else "mid"


def build(intraday, p):
    daily = intraday.resample("1D").agg({"open": "first", "high": "max",
            "low": "min", "close": "last", "volume": "sum"}).dropna()
    if len(daily) < p["inner_period"] + 5:
        return None
    C, O = daily["close"], daily["open"]
    mid, up_i, lo_i = bb(C, p["inner_period"], p["inner_sd"])
    _, up_o, lo_o = bb(C, p["outer_period"], p["outer_sd"])
    daily["mid"], daily["lo_i"], daily["up_i"], daily["up_o"] = mid, lo_i, up_i, up_o

    daily["bw"] = (up_i - lo_i) / mid
    daily["bw_pct"] = daily["bw"].rolling(p["bw_lookback"], min_periods=20).rank(pct=True)
    daily["regime"] = np.where(daily["bw_pct"] > p["trend_pct"], "trend",
                       np.where(daily["bw_pct"] < p["range_pct"], "range", "neutral"))

    daily["rev_raw"] = (daily["low"] <= daily["lo_i"]) & (C > daily["lo_i"]) & (C > O)
    daily["brk_raw"] = (C > daily["up_i"]) & (C > daily["up_o"]) & (C > O)
    daily["atr"] = true_range(daily).rolling(p["atr_period"]).mean()

    nodes = []
    for day, row in daily.iterrows():
        nodes.append(vp_node(intraday, day, row["close"], p)
                     if (row["rev_raw"] or row["brk_raw"]) else None)
    daily["vp"] = nodes
    daily["rev_conf"] = daily["rev_raw"] & (daily["vp"] == "HVN")
    # LVN-confirmed breakout: off by default (p["use_lvn"]); HVN is what we trade.
    daily["brk_conf"] = (daily["brk_raw"] & (daily["vp"] == "LVN")
                         if p.get("use_lvn", False) else False)
    return daily.reset_index()


def backtest(d, sig_col, p):
    O = d["open"].values
    H, L, Cl, A = d["high"].values, d["low"].values, d["close"].values, d["atr"].values
    MID = d["mid"].values
    sig, reg = d[sig_col].values, d["regime"].values
    slip = p["slip_bps"] / 1e4
    cost = p["cost_bps"] / 1e4
    next_open = p["entry_next_open"]
    exit_mode = p.get("exit_mode", "target")   # "target" (mean-revert) or "trail"

    out = []; i = 0; n = len(d)
    while i < n - 1:
        if not (sig[i] and not np.isnan(A[i])):
            i += 1
            continue
        if next_open:
            e_idx = i + 1
            if e_idx >= n:
                break
            raw_entry = O[e_idx]
        else:
            e_idx = i
            raw_entry = Cl[i]
        if np.isnan(raw_entry):
            i += 1
            continue
        entry = raw_entry * (1 + slip)

        # Decide exit style for THIS trade.
        # BREAKOUT trades: fixed-percentage scheme (no ATR).
        #   hard stop -brk_stop_pct, target +brk_target_pct (exit all),
        #   move stop to breakeven once price touches +brk_be_trigger_pct.
        # REVERSION trades: exit at the mean (mid-band) with a hard ATR stop.
        is_brk = sig_col.startswith("brk")
        target = MID[i]
        use_target = (exit_mode == "target") and (target > raw_entry)

        j = e_idx + 1; xp = None
        if is_brk:
            stop = raw_entry * (1 - p["brk_stop_pct"])
            tgt = raw_entry * (1 + p["brk_target_pct"])
            be_trig = raw_entry * (1 + p["brk_be_trigger_pct"])
            armed = False
            while j < n:
                # stop first (conservative); gap-through fills at the open
                if O[j] <= stop:
                    xp = O[j]; break
                if L[j] <= stop:
                    xp = stop; break
                # fixed target: exit ALL
                if O[j] >= tgt:
                    xp = O[j]; break          # gapped up through target
                if H[j] >= tgt:
                    xp = tgt; break
                # arm breakeven once +be_trigger is touched
                if not armed and H[j] >= be_trig:
                    stop = raw_entry; armed = True
                j += 1
        elif use_target:
            stop = raw_entry - p["stop_mult"] * A[i]
            while j < n:
                # stop checked first (conservative); gap-through fills at open
                if O[j] <= stop:
                    xp = O[j]; break
                if L[j] <= stop:
                    xp = stop; break
                # mean-reversion target: exit when price reaches the mid-band
                if O[j] >= target:
                    xp = O[j]; break          # gapped up through target -> open
                if H[j] >= target:
                    xp = target; break
                j += 1
        else:
            peak = raw_entry
            trail = raw_entry - p["atr_mult"] * A[i]
            while j < n:
                if O[j] <= trail:
                    xp = O[j]; break
                if L[j] <= trail:
                    xp = trail; break
                if H[j] > peak:
                    peak = H[j]
                    trail = max(trail, peak - p["atr_mult"] * A[j])
                j += 1
        if xp is None:
            xp = Cl[-1]; j = n - 1

        exit_px = xp * (1 - slip)
        gross = (xp - raw_entry) / raw_entry
        net = (exit_px - entry) / entry - cost
        out.append({"regime": reg[i], "bars": j - e_idx, "gross": gross, "ret": net})
        i = j + 1
    return pd.DataFrame(out)


def report(t, name):
    print(f"\n=== {name}  ({len(t)} trades) ===")
    if t.empty:
        print("  no trades"); return
    print(f"  {'regime':8}{'n':>4}{'win%':>7}{'avgNet%':>9}{'totNet%':>9}{'totGrs%':>9}")
    for reg in ["range", "trend", "neutral", "ALL"]:
        s = t if reg == "ALL" else t[t["regime"] == reg]
        if len(s) == 0:
            continue
        flag = "  <- thin" if (reg != "ALL" and len(s) < 30) else ""
        print(f"  {reg:8}{len(s):>4}{(s['ret']>0).mean()*100:>7.0f}"
              f"{s['ret'].mean()*100:>+9.2f}{s['ret'].sum()*100:>+9.1f}"
              f"{s['gross'].sum()*100:>+9.1f}{flag}")


# ---------------------------------------------------------------- driver
def resolve_symbols(args):
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.symbol:
        return [args.symbol.strip()]
    return list_symbols(args.db, args.unit, args.interval)   # ALL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--csv", help="use a CSV instead of the db")
    ap.add_argument("--symbol", help="single symbol (case-insensitive)")
    ap.add_argument("--symbols", help="comma-separated symbols (case-insensitive)")
    ap.add_argument("--unit", default="minutes")
    ap.add_argument("--interval", default="30", help="timeframe interval, e.g. 30 or 1")
    ap.add_argument("--start", help="YYYY-MM-DD inclusive (optional)")
    ap.add_argument("--end", help="YYYY-MM-DD inclusive (optional)")
    ap.add_argument("--atr-mult", type=float, default=PARAMS["atr_mult"])
    ap.add_argument("--exit", choices=["target", "trail"], default=PARAMS["exit_mode"],
                    help="target=exit at mean (mid-band); trail=ATR trailing stop")
    ap.add_argument("--stop-mult", type=float, default=PARAMS["stop_mult"],
                    help="hard stop = N x ATR below entry (target mode)")
    ap.add_argument("--use-lvn", action="store_true",
                    help="re-enable the LVN-confirmed breakout variant (off by default)")
    ap.add_argument("--use-raw-rev", action="store_true",
                    help="also take raw (mid/LVN) reversions; default is HVN-confirmed only")
    ap.add_argument("--brk-target", type=float, default=PARAMS["brk_target_pct"]*100,
                    help="breakout target %% (exit all), default 2")
    ap.add_argument("--brk-stop", type=float, default=PARAMS["brk_stop_pct"]*100,
                    help="breakout initial stop %%, default 2")
    ap.add_argument("--brk-be", type=float, default=PARAMS["brk_be_trigger_pct"]*100,
                    help="breakout breakeven trigger %%, default 1")
    ap.add_argument("--slip-bps", type=float, default=PARAMS["slip_bps"])
    ap.add_argument("--cost-bps", type=float, default=PARAMS["cost_bps"])
    ap.add_argument("--entry-on-close", action="store_true",
                    help="legacy: enter on signal close (lookahead) instead of next open")
    ap.add_argument("--out", default="unified_signals_v3.csv")
    args = ap.parse_args()
    PARAMS["atr_mult"] = args.atr_mult
    PARAMS["exit_mode"] = args.exit
    PARAMS["stop_mult"] = args.stop_mult
    PARAMS["use_lvn"] = args.use_lvn
    PARAMS["use_raw_rev"] = args.use_raw_rev
    PARAMS["brk_target_pct"] = args.brk_target / 100
    PARAMS["brk_stop_pct"] = args.brk_stop / 100
    PARAMS["brk_be_trigger_pct"] = args.brk_be / 100
    PARAMS["slip_bps"] = args.slip_bps
    PARAMS["cost_bps"] = args.cost_bps
    if args.entry_on_close:
        PARAMS["entry_next_open"] = False

    # ---- gather per-symbol daily frames ----
    if args.csv:
        intraday = load_csv(args.csv)
        d = build(intraday, PARAMS)
        if d is None:
            raise SystemExit("Not enough data after resampling.")
        d["symbol"] = "CSV"
        frames = [d]
        sym_list = ["CSV"]
    else:
        sym_list = resolve_symbols(args)
        if not sym_list:
            raise SystemExit("No symbols found for the given unit/interval.")
        frames = []
        for sym in sym_list:
            intraday = load_db_symbol(args.db, sym, args.unit, args.interval, args.start, args.end)
            if intraday.empty:
                print(f"  [skip] {sym}: no rows")
                continue
            d = build(intraday, PARAMS)
            if d is None:
                print(f"  [skip] {sym}: too few daily bars")
                continue
            d["symbol"] = sym.upper()
            frames.append(d)
        if not frames:
            raise SystemExit("No symbol produced usable data.")

    rng = f"{args.start or 'begin'} .. {args.end or 'end'}"
    entry_mode = "signal-close (LEGACY)" if not PARAMS["entry_next_open"] else "next-open"
    if PARAMS["exit_mode"] == "target":
        exit_desc = f"mean-revert target (mid-band), hard stop {PARAMS['stop_mult']}x ATR"
    else:
        exit_desc = f"{PARAMS['atr_mult']}x TR-ATR trailing stop"
    print(f"\nSymbols: {len(frames)} processed ({', '.join(f['symbol'].iloc[0] for f in frames)})")
    print(f"Timeframe: {args.unit}/{args.interval} | dates: {rng}")
    print(f"Fills: entry={entry_mode}, REV exit={exit_desc}, "
          f"slip {PARAMS['slip_bps']}bp/side, costs {PARAMS['cost_bps']}bp round-trip")
    print(f"       BRK exit=fixed +{PARAMS['brk_target_pct']*100:g}% target / "
          f"-{PARAMS['brk_stop_pct']*100:g}% stop / breakeven after +{PARAMS['brk_be_trigger_pct']*100:g}%")

    alld = pd.concat(frames, ignore_index=True)
    rc = alld["regime"].value_counts()
    print(f"Daily bars (pooled): {len(alld)} | regime mix: range {rc.get('range',0)}, "
          f"trend {rc.get('trend',0)}, neutral {rc.get('neutral',0)}")
    brk_conf_str = (f" -> conf {int(alld.brk_conf.sum())}" if PARAMS["use_lvn"] else " (LVN off)")
    rev_str = (f"rev raw {int(alld.rev_raw.sum())} -> HVN {int(alld.rev_conf.sum())}"
               if PARAMS["use_raw_rev"] else
               f"rev HVN-only {int(alld.rev_conf.sum())} (mid/LVN off)")
    print(f"Signals (pooled): {rev_str} | "
          f"brk raw {int(alld.brk_raw.sum())}{brk_conf_str}")

    # ---- pooled backtest across symbols ----
    # Reversion: HVN-confirmed only by default. mid/LVN reversions are not taken.
    variants = []
    if PARAMS["use_raw_rev"]:
        variants.append(("rev_raw", "REVERSION raw (incl. mid/LVN)"))
    variants.append(("rev_conf", "REVERSION confirmed (HVN)"))
    variants.append(("brk_raw", "BREAKOUT raw"))
    if PARAMS["use_lvn"]:
        variants.append(("brk_conf", "BREAKOUT confirmed (LVN)"))
    for sig_col, label in variants:
        trades = pd.concat([backtest(f, sig_col, PARAMS).assign(symbol=f["symbol"].iloc[0])
                            for f in frames], ignore_index=True)
        report(trades, label)

    print("\nHow to read:")
    print(" * avgNet/totNet are AFTER slippage + costs. totGrs is gross for the same trades.")
    print(" * trades are POOLED across all selected symbols. 'thin' = <30 trades in a cell.")
    print(" * confirmed NET trend row beats raw's -> volume profile earns its place.")
    print(" * tune --cost-bps/--slip-bps to your broker before trusting NET.")

    alld.to_csv(args.out, index=False)
    print(f"\nFull daily series + signals -> {args.out}")


if __name__ == "__main__":
    main()
