#!/usr/bin/env python3
"""
Sniper Agent Log Analyzer — Daily Post-Market Summary
======================================================

Parses sniper_YYYYMMDD.log and generates a clean post-market report:
- Trade summary (count, P&L, R-multiple stats per horizon)
- Weight evolution (what changed during the session)
- Calibration drift (confidence parameter changes)
- Gate rejection analysis (why weren't trades entered?)
- Alerts on concerning patterns

Usage:
  python3 sniper_agent_analyzer.py                    # Today's log
  python3 sniper_agent_analyzer.py 20260612          # Specific date
  python3 sniper_agent_analyzer.py 20260612 --csv    # CSV output
"""

import sys
import re
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import json

# ============================================================================
# Configuration
# ============================================================================
WORKSPACE_DIR = Path(__file__).parent
LOG_DIR = WORKSPACE_DIR.parent / "Data" / "SmartAgent" / "logs"

HORIZONS = ["intraday", "short_term", "swing", "positional"]

# ============================================================================
# Parsing Patterns
# ============================================================================

# [LEARN/horizon] R=+2.45 -> p*=0.68 | calib a=1.02 b=-0.15 | w={...}
LEARN_PATTERN = re.compile(
    r"\[LEARN/(\w+)\] R=([\+\-]?\d+\.\d+) -> p\*=([\d\.]+) \| "
    r"calib a=([\+\-]?\d+\.\d+) b=([\+\-]?\d+\.\d+)"
)

# ✅ [PROFIT/horizon] SYMBOL exit price | R=+1.89 | PnL=+5670.00
# ❌ [LOSS/horizon] SYMBOL exit price | R=-1.23 | PnL=-2450.00
EXIT_PATTERN = re.compile(
    r"(✅|❌) \[(\w+)/(\w+)\] (\w+) exit ([\d\.]+) \| R=([\+\-]?\d+\.\d+) \| PnL=([\+\-]?\d+\.?\d*)"
)

# [COST-GATE/horizon] SYMBOL: p=0.72 ... — skipped
GATE_PATTERNS = {
    "cost_gate": re.compile(r"\[COST-GATE/(\w+)\]"),
    "iv_cap": re.compile(r"\[IV-CAP/(\w+)\]"),
    "cooldown": re.compile(r"\[COOLDOWN/(\w+)\]"),
    "min_profit": re.compile(r"\[MIN-PROFIT/(\w+)\]"),
    "kelly_zero": re.compile(r"\[KELLY-ZERO/(\w+)\]"),
    "min_bars": re.compile(r"\[MIN-BARS/(\w+)\]"),
    "already_open": re.compile(r"\[ALREADY-OPEN/(\w+)\]"),
}

# ============================================================================
# Data Structures
# ============================================================================

class HorizonStats:
    def __init__(self):
        self.trades = []  # [{"outcome": PROFIT/LOSS, "symbol": X, "r_mult": 1.5, "pnl": 5000}, ...]
        self.learn_updates = []  # [{"r_mult": 1.5, "p_star": 0.68, "calib_a": 1.02, "calib_b": -0.15, "weights": {...}}, ...]
        self.gate_rejections = defaultdict(int)  # {"cost_gate": 5, "iv_cap": 2, ...}

class AnalysisResult:
    def __init__(self, date_str):
        self.date = date_str
        self.horizons = {h: HorizonStats() for h in HORIZONS}
        self.found_log = False
        self.log_file = None

# ============================================================================
# Parsing Functions
# ============================================================================

def parse_log_file(log_path):
    """Parse log file and extract all relevant data."""
    result = AnalysisResult(log_path.stem.replace("sniper_", ""))
    result.log_file = log_path
    result.found_log = True

    with open(log_path, 'r') as f:
        lines = f.readlines()

    for line in lines:
        # Parse LEARN updates
        m = LEARN_PATTERN.search(line)
        if m:
            horizon = m.group(1)
            if horizon in result.horizons:
                result.horizons[horizon].learn_updates.append({
                    "r_mult": float(m.group(2)),
                    "p_star": float(m.group(3)),
                    "calib_a": float(m.group(4)),
                    "calib_b": float(m.group(5)),
                })
            continue

        # Parse exit outcomes
        m = EXIT_PATTERN.search(line)
        if m:
            outcome = "PROFIT" if m.group(1) == "✅" else "LOSS"
            horizon = m.group(3)
            if horizon in result.horizons:
                result.horizons[horizon].trades.append({
                    "outcome": outcome,
                    "symbol": m.group(4),
                    "exit_price": float(m.group(5)),
                    "r_mult": float(m.group(6)),
                    "pnl": float(m.group(7)),
                })
            continue

        # Parse gate rejections
        for gate_name, pattern in GATE_PATTERNS.items():
            m = pattern.search(line)
            if m:
                horizon = m.group(1)
                if horizon in result.horizons:
                    result.horizons[horizon].gate_rejections[gate_name] += 1
                break

    return result

# ============================================================================
# Analysis Functions
# ============================================================================

def analyze_trades(horizon_stats):
    """Compute trade statistics."""
    trades = horizon_stats.trades
    if not trades:
        return None

    profits = [t for t in trades if t["outcome"] == "PROFIT"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]

    win_rate = len(profits) / len(trades) if trades else 0
    total_pnl = sum(t["pnl"] for t in trades)
    avg_r_mult = sum(t["r_mult"] for t in trades) / len(trades) if trades else 0
    best_r = max((t["r_mult"] for t in trades), default=0)
    worst_r = min((t["r_mult"] for t in trades), default=0)
    best_pnl = max((t["pnl"] for t in trades), default=0)
    worst_pnl = min((t["pnl"] for t in trades), default=0)

    return {
        "count": len(trades),
        "profits": len(profits),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_r_mult": avg_r_mult,
        "best_r": best_r,
        "worst_r": worst_r,
        "best_pnl": best_pnl,
        "worst_pnl": worst_pnl,
    }

def analyze_learning(horizon_stats):
    """Compute learning statistics."""
    updates = horizon_stats.learn_updates
    if not updates:
        return None

    p_stars = [u["p_star"] for u in updates]
    calib_as = [u["calib_a"] for u in updates]
    calib_bs = [u["calib_b"] for u in updates]

    return {
        "updates_count": len(updates),
        "p_star_start": p_stars[0] if p_stars else 0,
        "p_star_end": p_stars[-1] if p_stars else 0,
        "p_star_delta": (p_stars[-1] - p_stars[0]) if p_stars else 0,
        "calib_a_start": calib_as[0] if calib_as else 1.0,
        "calib_a_end": calib_as[-1] if calib_as else 1.0,
        "calib_a_delta": (calib_as[-1] - calib_as[0]) if calib_as else 0,
        "calib_b_start": calib_bs[0] if calib_bs else 0,
        "calib_b_end": calib_bs[-1] if calib_bs else 0,
        "calib_b_delta": (calib_bs[-1] - calib_bs[0]) if calib_bs else 0,
        "calib_a_range": (min(calib_as), max(calib_as)) if calib_as else (1.0, 1.0),
        "calib_b_range": (min(calib_bs), max(calib_bs)) if calib_bs else (0, 0),
    }

def analyze_gates(horizon_stats):
    """Compute gate rejection statistics."""
    rejections = horizon_stats.gate_rejections
    if not rejections:
        return None

    total = sum(rejections.values())
    return {
        "total_rejections": total,
        "breakdown": {k: v for k, v in sorted(rejections.items(), key=lambda x: x[1], reverse=True)},
    }

# ============================================================================
# Report Generation
# ============================================================================

def format_currency(val):
    """Format as Indian currency."""
    sign = "-" if val < 0 else "+"
    abs_val = abs(val)
    if abs_val >= 1_00_000:
        return f"{sign}₹{abs_val/1_00_000:.1f}L"
    elif abs_val >= 1_000:
        return f"{sign}₹{abs_val/1_000:.1f}K"
    else:
        return f"{sign}₹{abs_val:.0f}"

def print_report(result):
    """Generate and print the analysis report."""
    if not result.found_log:
        print(f"❌ No log file found for {result.date}")
        return

    print("=" * 80)
    print(f"  SNIPER AGENT — POST-MARKET ANALYSIS")
    print(f"  Date: {result.date}")
    print("=" * 80)
    print()

    # Summary across all horizons
    total_trades = sum(len(h.trades) for h in result.horizons.values())
    total_pnl = sum(sum(t["pnl"] for t in h.trades) for h in result.horizons.values())
    total_profits = sum(len([t for t in h.trades if t["outcome"] == "PROFIT"]) for h in result.horizons.values())
    total_losses = sum(len([t for t in h.trades if t["outcome"] == "LOSS"]) for h in result.horizons.values())

    print("📊 OVERALL SUMMARY")
    print("-" * 80)
    print(f"  Total Trades:     {total_trades}")
    if total_trades > 0:
        print(f"  Wins/Losses:      {total_profits}W / {total_losses}L ({100*total_profits/total_trades:.1f}% win rate)")
        print(f"  Total P&L:        {format_currency(total_pnl)}")
    else:
        print(f"  (No trades executed)")
    print()

    # Per-horizon detailed breakdown
    print("📈 PER-HORIZON BREAKDOWN")
    print("-" * 80)

    for horizon in HORIZONS:
        h_stats = result.horizons[horizon]

        # Trade stats
        trade_analysis = analyze_trades(h_stats)
        learn_analysis = analyze_learning(h_stats)
        gate_analysis = analyze_gates(h_stats)

        print(f"\n  {horizon.upper()}")
        print(f"  {'-' * 76}")

        if trade_analysis:
            print(f"    Trades:        {trade_analysis['count']} total "
                  f"({trade_analysis['profits']}W / {trade_analysis['losses']}L, "
                  f"{100*trade_analysis['win_rate']:.0f}% win rate)")
            print(f"    P&L:           {format_currency(trade_analysis['total_pnl'])}")
            print(f"    R-multiple:    avg {trade_analysis['avg_r_mult']:+.2f}R, "
                  f"best {trade_analysis['best_r']:+.2f}R, "
                  f"worst {trade_analysis['worst_r']:+.2f}R")
        else:
            print(f"    Trades:        None")

        if learn_analysis:
            p_delta_icon = "📈" if learn_analysis['p_star_delta'] < 0 else "📉"
            a_icon = "⚠️ " if abs(learn_analysis['calib_a_delta']) > 0.3 else "✓ "
            b_icon = "⚠️ " if abs(learn_analysis['calib_b_delta']) > 0.5 else "✓ "

            print(f"    Learning:      {learn_analysis['updates_count']} trade(s) closed")
            print(f"    p*-threshold:  {learn_analysis['p_star_start']:.3f} → {learn_analysis['p_star_end']:.3f} "
                  f"({p_delta_icon}{learn_analysis['p_star_delta']:+.3f})")
            print(f"    {a_icon}calib_a:       {learn_analysis['calib_a_start']:.2f} → {learn_analysis['calib_a_end']:.2f} "
                  f"({learn_analysis['calib_a_delta']:+.2f}, range [{learn_analysis['calib_a_range'][0]:.2f}, "
                  f"{learn_analysis['calib_a_range'][1]:.2f}])")
            print(f"    {b_icon}calib_b:       {learn_analysis['calib_b_start']:.2f} → {learn_analysis['calib_b_end']:.2f} "
                  f"({learn_analysis['calib_b_delta']:+.2f}, range [{learn_analysis['calib_b_range'][0]:.2f}, "
                  f"{learn_analysis['calib_b_range'][1]:.2f}])")
        else:
            print(f"    Learning:      No trades closed (no learning)")

        if gate_analysis:
            print(f"    Gate rejections: {gate_analysis['total_rejections']} entries rejected")
            for gate_name, count in gate_analysis['breakdown'].items():
                print(f"      • {gate_name}: {count}")
        else:
            print(f"    Gate rejections: None")

    print()
    print("=" * 80)
    print(f"  Report generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

# ============================================================================
# Alerts & Diagnostics
# ============================================================================

def check_alerts(result):
    """Check for concerning patterns."""
    alerts = []

    for horizon in HORIZONS:
        h_stats = result.horizons[horizon]
        learn_analysis = analyze_learning(h_stats)

        if not learn_analysis:
            continue

        # Calibration runaway
        if abs(learn_analysis['calib_a_delta']) > 0.5:
            alerts.append(
                f"⚠️  {horizon}: calib_a drifted {learn_analysis['calib_a_delta']:+.2f} "
                f"(overconfidence tuning away — watch for overfitting)"
            )

        if abs(learn_analysis['calib_b_delta']) > 1.0:
            alerts.append(
                f"⚠️  {horizon}: calib_b drifted {learn_analysis['calib_b_delta']:+.2f} "
                f"(baseline bias shifting — recalibration in progress)"
            )

        # p* threshold runaway
        if learn_analysis['p_star_end'] >= 0.79:
            alerts.append(
                f"⚠️  {horizon}: p* threshold at {learn_analysis['p_star_end']:.3f} "
                f"(near max 0.80 — too many losses, bar very high)"
            )

        if learn_analysis['p_star_end'] <= 0.56:
            alerts.append(
                f"⚠️  {horizon}: p* threshold at {learn_analysis['p_star_end']:.3f} "
                f"(near min 0.55 — too many wins, bar very low, risk of overfitting)"
            )

        # High gate rejection rate
        gate_analysis = analyze_gates(h_stats)
        trade_analysis = analyze_trades(h_stats)
        if gate_analysis and trade_analysis:
            total_evaluated = trade_analysis['count'] + gate_analysis['total_rejections']
            reject_pct = 100 * gate_analysis['total_rejections'] / total_evaluated
            if reject_pct > 70:
                alerts.append(
                    f"⚠️  {horizon}: {reject_pct:.0f}% gate rejection rate "
                    f"({gate_analysis['total_rejections']} rejected, {trade_analysis['count']} entered)"
                )

    if alerts:
        print()
        print("🚨 ALERTS & DIAGNOSTICS")
        print("-" * 80)
        for alert in alerts:
            print(f"  {alert}")
        print()

# ============================================================================
# Main
# ============================================================================

def main():
    if len(sys.argv) < 2:
        # Use today's date
        today = datetime.now().strftime("%Y%m%d")
        date_str = today
    else:
        date_str = sys.argv[1]

    # Construct log file path
    log_path = LOG_DIR / f"sniper_{date_str}.log"

    if not log_path.exists():
        print(f"❌ Log file not found: {log_path}")
        print(f"\nAvailable logs:")
        for log_file in sorted(LOG_DIR.glob("sniper_*.log"), reverse=True)[:10]:
            print(f"  {log_file.stem}")
        return 1

    # Parse and analyze
    result = parse_log_file(log_path)
    print_report(result)
    check_alerts(result)

    return 0

if __name__ == "__main__":
    sys.exit(main())
