"""
PriceActionAgent backtest harness — daily EOD scan, BUY signals, structural exits.

Reuses:
  - marketdata.db (same as RealBackTest fetch / PriceActionAgent data_loader)
  - SmartEngine run_smart_synthesis (default DECISION_ENGINE=smart)
  - nse_calendar for trading-day iteration

Does NOT reuse Hermes run_replay (1-min grid, ATR barriers, options plane).
Run data fetch first:
  python RealBackTest/realbacktest.py fetch --equity-only --start 2024-06-01 --end 2026-06-10
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

_PA = Path(__file__).resolve().parent
_REPO = _PA.parent
if str(_PA) not in sys.path:
    sys.path.insert(0, str(_PA))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@dataclass
class PASignal:
    symbol: str
    session_date: str
    decision_date: str
    setup: str
    entry: float
    target: float
    stop_loss: float
    rejection: str | None = None


@dataclass
class PATrade:
    symbol: str
    setup: str
    session_date: str
    decision_date: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    initial_stop: float
    target: float
    outcome: str  # win | loss | trail_loss | timeout | no_fill
    bars_held: int
    pnl: float
    pnl_net: float
    r_gross: float
    r_net: float
    qty: int = 1
    meta: dict = field(default_factory=dict)


def _synthesis_for_session(symbol: str, session_date: datetime.date) -> dict | None:
    from analytics import build_vwap_block, build_volume_profile_block, compute_nifty_context
    from config import ROLLING_DAYS
    from data_loader import get_symbol_data_window, load_nifty_daily
    from nse_calendar import next_trading_day
    from synthesis import run_smart_synthesis_path

    try:
        daily, weekly, h4, anchors, ws, we = get_symbol_data_window(
            symbol, end_date=session_date, rolling_days=ROLLING_DAYS,
        )
    except ValueError:
        return None
    if len(daily) < 20:
        return None

    nifty = load_nifty_daily(ws, we)
    vp = build_volume_profile_block(daily)
    vw = build_vwap_block(daily, [], anchors.last_close)
    nc = compute_nifty_context(daily, nifty) if nifty else None
    am = {
        "last_close": anchors.last_close,
        "last_date": anchors.last_date,
        "ma_50d": anchors.ma_50d,
        "ma_20d": anchors.ma_20d,
        "avg_vol_20d": anchors.avg_vol_20d,
    }
    return run_smart_synthesis_path(
        symbol=symbol,
        window_start=ws.isoformat(),
        window_end=we.isoformat(),
        next_td=next_trading_day(we).isoformat(),
        weekly_candles=weekly,
        daily_candles=daily,
        h4_candles=h4,
        anchor_metrics_dict=am,
        vwap_block=vw,
        volume_profile_block=vp,
        nifty_ctx=nc,
    )


def collect_signals(
    symbols: list[str],
    start: datetime.date,
    end: datetime.date,
) -> list[PASignal]:
    from nse_calendar import trading_days_between

    signals: list[PASignal] = []
    days = trading_days_between(start, end)
    log.info("Scanning %d symbols × %d session days [%s → %s]",
             len(symbols), len(days), start, end)

    for i, session_date in enumerate(days, 1):
        if i % 20 == 1:
            log.info("  session progress %d/%d (%s)", i, len(days), session_date)
        for symbol in symbols:
            out = _synthesis_for_session(symbol, session_date)
            if not out:
                continue
            td = out.get("trade_decision") or {}
            if td.get("action") != "BUY":
                continue
            entry, target, stop = td.get("entry"), td.get("target"), td.get("stop_loss")
            if not all(isinstance(x, (int, float)) and x > 0 for x in (entry, target, stop)):
                continue
            if target <= entry or entry <= stop:
                continue
            from nse_calendar import next_trading_day
            signals.append(PASignal(
                symbol=symbol,
                session_date=session_date.isoformat(),
                decision_date=next_trading_day(session_date).isoformat(),
                setup=td.get("setup") or "UNKNOWN",
                entry=float(entry),
                target=float(target),
                stop_loss=float(stop),
                rejection=td.get("rejection"),
            ))
    log.info("Collected %d BUY signals", len(signals))
    return signals


def _load_daily_from(symbol: str, from_date: datetime.date, to_date: datetime.date):
    from data_loader import load_daily_candles
    return load_daily_candles(symbol, from_date, to_date)


def _r_trail(
    entry: float,
    high_water: float,
    init_stop: float,
    activate_r: float,
    trail_r_mult: float,
    current: float,
) -> float:
    """Volatility-scaled trailing stop, activated only after the trade works.

    The trade's own risk unit R = (entry - init_stop) is the volatility yardstick
    (structural stops are already volatility-aware, so R behaves like an ATR
    multiple). Behaviour:
      * While profit < activate_r * R: only the structural stop is live — let the
        trade breathe; do NOT trail on noise.
      * Once high_water reaches entry + activate_r*R: trail the stop trail_r_mult*R
        below the high-water mark, ratcheting up, never below the structural stop.
    Defaults activate_r=1.0, trail_r_mult=1.0 → "after +1R, trail by 1R."
    """
    risk = entry - init_stop
    if risk <= 0:
        return max(current, init_stop)
    if high_water < entry + activate_r * risk:
        return max(current, init_stop)
    locked = high_water - trail_r_mult * risk
    return max(current, init_stop, locked)


def simulate_buy_trade(
    sig: PASignal,
    *,
    activate_r: float = 1.0,
    trail_r_mult: float = 1.0,
    max_bars: int = 20,
    cost_bps: float = 10.0,
    capital_per_trade: float = 100_000.0,
) -> PATrade | None:
    """
    Enter on decision_date when price touches entry; walk daily bars with
    target + initial stop + an R-multiple trailing stop that activates only
    after the trade is +activate_r*R in profit, then trails trail_r_mult*R below
    the high-water mark.
    """
    from data_loader import Candle

    dec = datetime.date.fromisoformat(sig.decision_date)
    end = dec + datetime.timedelta(days=int(max_bars * 1.6) + 10)
    bars = _load_daily_from(sig.symbol, dec, end)
    if not bars:
        return None

    filled = False
    entry_price = sig.entry
    entry_date = sig.decision_date
    qty = max(1, int(capital_per_trade / entry_price))

    trail_stop = sig.stop_loss
    high_water = entry_price
    forward: list[Candle] = []

    for bar in bars:
        bar_d = bar.date[:10]
        if bar_d < sig.decision_date:
            continue
        if not filled:
            if bar.low <= sig.entry <= bar.high:
                filled = True
                entry_price = sig.entry
                entry_date = bar_d
                high_water = max(high_water, bar.high)
                trail_stop = _r_trail(entry_price, high_water, sig.stop_loss, activate_r, trail_r_mult, trail_stop)
                forward.append(bar)
                continue
            if bar.open <= sig.entry:
                filled = True
                entry_price = min(bar.open, sig.entry)
                entry_date = bar_d
                high_water = max(high_water, bar.high)
                trail_stop = _r_trail(entry_price, high_water, sig.stop_loss, activate_r, trail_r_mult, trail_stop)
                forward.append(bar)
                continue
            continue

        high_water = max(high_water, bar.high)
        trail_stop = _r_trail(entry_price, high_water, sig.stop_loss, activate_r, trail_r_mult, trail_stop)

        if bar.low <= trail_stop:
            exit_px = min(trail_stop, bar.open) if bar.open < trail_stop else trail_stop
            outcome = "trail_loss" if trail_stop > sig.stop_loss else "loss"
            return _close_trade(sig, entry_date, bar_d, entry_price, exit_px, qty, outcome,
                                len(forward) + 1, cost_bps,
                                {"trail_stop": trail_stop, "high_water": high_water})
        if bar.high >= sig.target:
            exit_px = max(sig.target, bar.open) if bar.open > sig.target else sig.target
            return _close_trade(sig, entry_date, bar_d, entry_price, exit_px, qty, "win",
                                len(forward) + 1, cost_bps,
                                {"trail_stop": trail_stop, "high_water": high_water})

        forward.append(bar)
        if len(forward) >= max_bars:
            exit_px = bar.close
            return _close_trade(sig, entry_date, bar_d, entry_price, exit_px, qty, "timeout",
                                max_bars, cost_bps,
                                {"trail_stop": trail_stop, "high_water": high_water})

    if not filled:
        return PATrade(
            symbol=sig.symbol, setup=sig.setup,
            session_date=sig.session_date, decision_date=sig.decision_date,
            entry_date="", exit_date="",
            entry_price=sig.entry, exit_price=0.0,
            initial_stop=sig.stop_loss, target=sig.target,
            outcome="no_fill", bars_held=0,
            pnl=0.0, pnl_net=0.0, r_gross=0.0, r_net=0.0, qty=qty,
        )
    return None


_R_RISK_FLOOR_PCT = 0.5  # floor risk distance at 0.5% of entry so R can't explode on gap fills


def _close_trade(sig, entry_date, exit_date, entry_px, exit_px, qty, outcome, bars, cost_bps, meta):
    # Floor the risk distance: on gap fills entry_px can land a hair from the
    # stop, making the true risk ~0 and R meaningless (e.g. -200R on a -1% move).
    risk_dist = max(entry_px - sig.stop_loss, entry_px * _R_RISK_FLOOR_PCT / 100.0)
    risk = risk_dist * qty
    pnl = (exit_px - entry_px) * qty
    cost = cost_bps / 1e4 * entry_px * qty * 2.0
    pnl_net = pnl - cost
    r_gross = pnl / risk if risk > 1e-9 else 0.0
    r_net = pnl_net / risk if risk > 1e-9 else 0.0
    return PATrade(
        symbol=sig.symbol, setup=sig.setup,
        session_date=sig.session_date, decision_date=sig.decision_date,
        entry_date=entry_date, exit_date=exit_date,
        entry_price=round(entry_px, 2), exit_price=round(exit_px, 2),
        initial_stop=sig.stop_loss, target=sig.target,
        outcome=outcome, bars_held=bars,
        pnl=round(pnl, 2), pnl_net=round(pnl_net, 2),
        r_gross=round(r_gross, 3), r_net=round(r_net, 3),
        qty=qty, meta=meta,
    )


def _symbols_with_daily_data(symbols: list[str]) -> list[str]:
    import sqlite3
    from config import MARKET_DATA_DB
    if not Path(MARKET_DATA_DB).exists():
        return symbols
    conn = sqlite3.connect(MARKET_DATA_DB)
    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM equity_candles WHERE unit='days'"
    ).fetchall()}
    conn.close()
    out = [s for s in symbols if s in have]
    missing = len(symbols) - len(out)
    if missing:
        log.warning("%d symbols have no daily data in marketdata.db — skipped", missing)
    return out


def run_backtest(
    symbols: list[str],
    start: datetime.date,
    end: datetime.date,
    *,
    activate_r: float = 1.0,
    trail_r_mult: float = 1.0,
    max_bars: int = 20,
    cost_bps: float = 10.0,
    capital_per_trade: float = 100_000.0,
    tag: str = "pa_main",
) -> dict:
    symbols = _symbols_with_daily_data(symbols)
    t0 = time.time()
    signals = collect_signals(symbols, start, end)
    trades: list[PATrade] = []
    for sig in signals:
        t = simulate_buy_trade(
            sig, activate_r=activate_r, trail_r_mult=trail_r_mult, max_bars=max_bars,
            cost_bps=cost_bps, capital_per_trade=capital_per_trade,
        )
        if t:
            trades.append(t)

    elapsed = time.time() - t0
    out_dir = _REPO / "Data" / "PriceActionAgent" / "backtests" / tag
    out_dir.mkdir(parents=True, exist_ok=True)

    trades_path = out_dir / "trades.csv"
    signals_path = out_dir / "signals.json"
    summary_path = out_dir / "summary.json"

    with trades_path.open("w", newline="") as f:
        if trades:
            w = csv.DictWriter(f, fieldnames=list(asdict(trades[0]).keys()))
            w.writeheader()
            for t in trades:
                row = asdict(t)
                row["meta"] = json.dumps(row["meta"])
                w.writerow(row)

    signals_path.write_text(json.dumps([asdict(s) for s in signals], indent=2))
    summary = _summarize(trades, signals, symbols, start, end, elapsed, {
        "activate_r": activate_r, "trail_r_mult": trail_r_mult, "max_bars": max_bars,
        "cost_bps": cost_bps, "capital_per_trade": capital_per_trade, "tag": tag,
    })
    summary_path.write_text(json.dumps(summary, indent=2))

    report_path = out_dir / f"pa_backtest_report_{tag}.md"
    report_path.write_text(_write_report(summary, trades))

    return {"trades": trades, "signals": signals, "summary": summary,
            "out_dir": str(out_dir), "report": str(report_path)}


def _summarize(trades, signals, symbols, start, end, elapsed, params) -> dict:
    filled = [t for t in trades if t.outcome != "no_fill"]
    r = np.array([t.r_net for t in filled]) if filled else np.array([])
    wins = [t for t in filled if t.r_net > 0]
    by_setup: dict[str, list] = {}
    for t in filled:
        by_setup.setdefault(t.setup, []).append(t.r_net)

    return {
        "params": params,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "universe_size": len(symbols),
        "runtime_s": round(elapsed, 1),
        "signals_buy": len(signals),
        "trades_total": len(trades),
        "trades_filled": len(filled),
        "no_fill": sum(1 for t in trades if t.outcome == "no_fill"),
        "hit_rate": float(np.mean(r > 0)) if len(r) else 0.0,
        "mean_r_net": float(np.mean(r)) if len(r) else 0.0,
        "total_pnl_net": float(sum(t.pnl_net for t in filled)),
        "profit_factor": (
            abs(sum(t.pnl_net for t in wins)) / abs(sum(t.pnl_net for t in filled if t.r_net <= 0))
            if filled and any(t.r_net <= 0 for t in filled) else None
        ),
        "max_drawdown_pct": _max_dd(filled),
        "by_setup": {
            k: {"n": len(v), "mean_r": float(np.mean(v)), "hit": float(np.mean(np.array(v) > 0))}
            for k, v in by_setup.items()
        },
        "by_outcome": _count_outcomes(trades),
    }


def _count_outcomes(trades):
    out: dict[str, int] = {}
    for t in trades:
        out[t.outcome] = out.get(t.outcome, 0) + 1
    return out


def _max_dd(trades: list[PATrade]) -> float:
    if not trades:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x.exit_date or x.decision_date):
        cum += t.pnl_net
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return round(max_dd, 2)


def _write_report(summary: dict, trades: list[PATrade]) -> str:
    p = summary["params"]
    lines = [
        f"# PriceActionAgent Backtest Report — `{p['tag']}`",
        "",
        f"Generated: {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        "",
        "## Period & universe",
        f"- **Window:** {summary['period']['start']} → {summary['period']['end']} (session dates)",
        f"- **Symbols scanned:** {summary['universe_size']}",
        f"- **Engine:** SmartEngine (`DECISION_ENGINE=smart`)",
        f"- **Direction filter:** BUY only",
        "",
        "## Simulation rules",
        f"- Entry: limit touch at signal `entry` on `decision_date` (next session)",
        f"- Exit: structural `target` OR initial `stop_loss` OR R-multiple trailing "
        f"stop (activates after +{p.get('activate_r', 1.0)}R, trails {p.get('trail_r_mult', 1.0)}R below high-water)",
        f"- Timeout: {p['max_bars']} daily bars",
        f"- Costs: {p['cost_bps']} bps round-trip",
        f"- Size: ₹{p['capital_per_trade']:,.0f} notional per trade",
        "",
        "## Headline results",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| BUY signals | {summary['signals_buy']} |",
        f"| Trades (filled) | {summary['trades_filled']} |",
        f"| No-fill | {summary['no_fill']} |",
        f"| Hit rate (R>0) | {summary['hit_rate']:.1%} |",
        f"| Mean R (net) | {summary['mean_r_net']:.3f} |",
        f"| Total PnL (net) | ₹{summary['total_pnl_net']:,.0f} |",
        f"| Profit factor | {summary['profit_factor'] or '—'} |",
        f"| Max equity drawdown | ₹{summary['max_drawdown_pct']:,.0f} |",
        f"| Runtime | {summary['runtime_s']}s |",
        "",
        "## Outcomes",
    ]
    for k, v in summary.get("by_outcome", {}).items():
        lines.append(f"- **{k}:** {v}")

    lines.extend(["", "## Per-setup", "| Setup | N | Hit rate | Mean R |", "|-------|---|----------|--------|"])
    for setup, st in sorted(summary.get("by_setup", {}).items()):
        lines.append(f"| {setup} | {st['n']} | {st['hit']:.1%} | {st['mean_r']:.3f} |")

    lines.extend([
        "",
        "## Relation to RealBackTest",
        "- **Shared:** `marketdata.db` populated via `realbacktest.py fetch`",
        "- **Not shared:** Hermes 1-min replay, options plane, ATR triple-barrier exits",
        "- **Fetch before backtest:**",
        "  ```bash",
        "  python RealBackTest/realbacktest.py fetch --equity-only --start 2024-06-01 --end 2026-06-10",
        "  ```",
        "",
        "## Top / bottom trades (net R)",
    ])
    filled = [t for t in trades if t.outcome != "no_fill"]
    filled.sort(key=lambda x: x.r_net, reverse=True)
    for label, subset in [("Best 5", filled[:5]), ("Worst 5", filled[-5:])]:
        lines.append(f"\n### {label}")
        for t in subset:
            lines.append(
                f"- {t.symbol} {t.setup} {t.session_date}: R={t.r_net:+.2f} "
                f"({t.outcome}) entry {t.entry_price} → exit {t.exit_price}"
            )
    return "\n".join(lines) + "\n"


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from config import NSE100_SYMBOLS

    ap = argparse.ArgumentParser(description="PriceActionAgent daily BUY backtest")
    ap.add_argument("--start", default="2024-10-01", help="First session date (Q4 2024)")
    ap.add_argument("--end", default="2026-03-31", help="Last session date (Q1 2026)")
    ap.add_argument("--symbols", default=None, help="Comma list; default NSE100")
    ap.add_argument("--activate-r", type=float, default=1.0, help="Activate trail after +Nx initial risk (R)")
    ap.add_argument("--trail-r-mult", type=float, default=1.0, help="Trail Nx initial risk below high-water")
    ap.add_argument("--max-bars", type=int, default=20, help="Max hold (daily bars)")
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--tag", default="pa_q4_2024_q1_2026")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else NSE100_SYMBOLS
    start, end = _parse_date(args.start), _parse_date(args.end)

    result = run_backtest(
        symbols, start, end,
        activate_r=args.activate_r,
        trail_r_mult=args.trail_r_mult,
        max_bars=args.max_bars,
        cost_bps=args.cost_bps,
        capital_per_trade=args.capital,
        tag=args.tag,
    )
    s = result["summary"]
    print(f"\nReport: {result['report']}")
    print(f"Signals: {s['signals_buy']} | Filled: {s['trades_filled']} | "
          f"Hit: {s['hit_rate']:.1%} | Mean R: {s['mean_r_net']:.3f} | "
          f"PnL: ₹{s['total_pnl_net']:,.0f}")


if __name__ == "__main__":
    main()
