#!/usr/bin/env python3
"""
==============================================================================
 REALBACKTEST — real-NSE-data battle harness for the Sniper Agent
==============================================================================

Trains and judges SmartAgent/allstrategy.py (unmodified) on REAL Upstox
history instead of synthetic markets:

  bars     real 1-min / 30-min / daily / weekly candles per symbol (V3 API)
  options  real expired-contract 1-min candles with OI; the greeks the API
           does not provide are reconstructed:
             IV   Black-Scholes inversion of the real ATM premium
             PCR  real per-minute CE/PE open interest
             flow Bulk Volume Classification proxy (A/B'd via ablation)

Workflow (run on your machine — needs UPSTOX_ACCESS_TOKEN):

  python realbacktest.py selftest                      # trust the pipeline
  python realbacktest.py fetch  [--start --end]        # fill the cache
  python realbacktest.py audit                         # coverage check
  python realbacktest.py run    [--start --end] [...]  # backtest + report

`run` executes: main pass -> flow-ablation pass -> benchmarks (random-entry
null, bootstrap, deflated Sharpe, buy & hold) -> reports/realworthiness_
report_<tag>.md with a GO / NO-GO verdict for live promotion.

Outputs land in RealBackTest/reports/ ; per-run engine DBs in RealBackTest/db/.
==============================================================================
"""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rbt import config as C                                  # noqa: E402


def _resolve_token() -> str:
    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip().strip("'\"")
    if token and token != "YOUR_UPSTOX_ACCESS_TOKEN":
        return token
    token_file = Path.home() / ".sniper_token"
    if token_file.exists():
        token = token_file.read_text().strip().strip("'\"")
        if token:
            return token
    return ""


def cmd_fetch(a):
    if a.universe:
        os.environ["RBT_UNIVERSE"] = a.universe
    from rbt.upstox_data import fetch_all
    token = _resolve_token()
    tfs = a.timeframes.split(",") if a.timeframes else None
    fetch_all(token, a.start, a.end,
              symbols=a.symbols.split(",") if a.symbols else None,
              include_options=not a.equity_only,
              timeframes=tfs)


def cmd_audit(_a):
    from rbt.upstox_data import audit
    audit()


def cmd_selftest(_a):
    from rbt.selftest import main as st
    sys.exit(st())


def cmd_run(a):
    from rbt.harness import SymbolData, run_replay, save_run
    from rbt.report import go_no_go, run_assessment, write_report
    from rbt.upstox_data import Cache, audit
    import rbt.harness as H

    if getattr(a, "legacy", False):       # A/B: v1 behaviour (Hermes v2 off)
        for k in H.S.V2:
            H.S.V2[k] = False
        print("[RUN] HERMES V2 DISABLED (--legacy): v1 baseline behaviour")

    horizons = a.horizons.split(",")
    symbols = a.symbols.split(",") if a.symbols else None
    run_dir = C.REPORT_DIR / "runs" / a.tag

    print(f"[RUN] main pass {a.start}..{a.end} horizons={horizons} "
          f"cost={a.cost_bps}bps")
    main_res = run_replay(a.start, a.end, horizons, symbols=symbols,
                          cost_bps=a.cost_bps, tag=a.tag,
                          engine_log=str(run_dir / "engine_main.log")
                          if a.engine_log else None)
    save_run(main_res, run_dir)
    print(f"[RUN] main pass: {len(main_res['trades'])} trades")

    abl_res = None
    if not a.no_ablation:
        print("[RUN] flow-ablation pass (x6 = 0)")
        abl_res = run_replay(a.start, a.end, horizons, symbols=symbols,
                             cost_bps=a.cost_bps, flow_ablation=True,
                             tag=f"{a.tag}_ablation")
        save_run(abl_res, run_dir / "ablation")
        print(f"[RUN] ablation pass: {len(abl_res['trades'])} trades")

    cache = Cache()
    syms = {u["symbol"]: SymbolData(cache, u) for u in C.UNIVERSE
            if (symbols is None or u["symbol"] in symbols)}
    audit_data = audit(log=lambda *_: None)
    assess = run_assessment(main_res, abl_res, syms, H.S, audit_data,
                            n_trials=a.n_trials)
    path = write_report(main_res, abl_res, assess, audit_data, H.S,
                        tag=a.tag)
    verdict, _ = go_no_go(assess, main_res, H.S.CAPITAL)
    print(f"\n[REPORT] {path}\n[VERDICT] {verdict}")


def cmd_run_pa(a):
    """PriceActionAgent daily BUY backtest (SmartEngine, structural exits)."""
    import sys
    from pathlib import Path
    pa = Path(__file__).resolve().parent.parent / "PriceActionAgent"
    sys.path.insert(0, str(pa))
    from pa_backtest import main as pa_main
    sys.argv = [
        "pa_backtest",
        "--start", a.start,
        "--end", a.end,
        "--trail-pct", str(a.trail_pct),
        "--max-bars", str(a.max_bars),
        "--cost-bps", str(a.cost_bps),
        "--capital", str(a.capital),
        "--tag", a.tag,
    ]
    if a.symbols:
        sys.argv.extend(["--symbols", a.symbols])
    pa_main()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download + cache real Upstox history")
    f.add_argument("--start", default=C.DEFAULT_START)
    f.add_argument("--end", default=C.DEFAULT_END)
    f.add_argument("--symbols", default=None,
                   help="comma list, default: full universe")
    f.add_argument("--equity-only", action="store_true",
                   help="stage 1: bars only, skip the options plane "
                        "(re-run without this flag to add options)")
    f.add_argument("--universe", default=None,
                   choices=["nse100", "nse500"],
                   help="symbol universe file (default: nse100)")
    f.add_argument("--timeframes", default=None,
                   help="comma list: 30m,daily,weekly (default: all incl 1m)")
    f.set_defaults(fn=cmd_fetch)

    sub.add_parser("audit", help="cache coverage report").set_defaults(
        fn=cmd_audit)
    sub.add_parser("selftest", help="offline pipeline verification"
                   ).set_defaults(fn=cmd_selftest)

    r = sub.add_parser("run", help="backtest + assessment + report")
    r.add_argument("--start", default=C.DEFAULT_START)
    r.add_argument("--end", default=C.DEFAULT_END)
    r.add_argument("--horizons",
                   default="intraday,short_term,swing,positional")
    r.add_argument("--symbols", default=None)
    r.add_argument("--cost-bps", type=float, default=C.COST_BPS_DEFAULT)
    r.add_argument("--tag", default="main")
    r.add_argument("--no-ablation", action="store_true")
    r.add_argument("--legacy", action="store_true",
                   help="disable all Hermes V2 abilities (v1 baseline A/B)")
    r.add_argument("--engine-log", action="store_true",
                   help="keep the full engine stdout (large)")
    r.add_argument("--n-trials", type=int, default=10,
                   help="effective strategy variants tried, for DSR")
    r.set_defaults(fn=cmd_run)

    pa = sub.add_parser("run-pa",
                        help="PriceActionAgent daily BUY backtest (SmartEngine)")
    pa.add_argument("--start", default="2024-10-01")
    pa.add_argument("--end", default="2026-03-31")
    pa.add_argument("--symbols", default=None)
    pa.add_argument("--trail-pct", type=float, default=2.0)
    pa.add_argument("--max-bars", type=int, default=20)
    pa.add_argument("--cost-bps", type=float, default=10.0)
    pa.add_argument("--capital", type=float, default=100_000.0)
    pa.add_argument("--tag", default="pa_q4_2024_q1_2026")
    pa.set_defaults(fn=cmd_run_pa)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
