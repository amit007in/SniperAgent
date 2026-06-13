#!/usr/bin/env python3
"""
seed_live_brain.py — seed the live agent brain state from an approved backtest.
================================================================================

Copies the final per-horizon brain state (weights, p*, calibration) from a
backtest run DB into the live agent DB. The live agent then continues learning
from that starting point instead of starting cold.

What IS copied:
  brain_state rows — Bayesian weights, p*, Platt calibration (a, b),
                     trade count, win count per horizon

What is NOT copied:
  trades        — historical backtest trades are not live trades
  open_positions — backtest positions are not real open positions

Usage:
  cd "/Users/amitkumar/Personal/work/source code/SniperAgent/DailyFetch"

  # 1. Run the seed-period backtest first (Q4-2024 → Q1-2026):
  #    cd ../RealBackTest
  #    python3 realbacktest.py run \\
  #        --start 2024-10-01 --end 2026-03-31 \\
  #        --tag seed_brain --no-ablation
  #
  # 2. Inspect the brain state (printed by this script before writing):
  #    python3 seed_live_brain.py --dry-run
  #
  # 3. Approve and seed:
  #    python3 seed_live_brain.py

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO     = Path(__file__).resolve().parent.parent
RBT_DIR  = REPO / "RealBackTest"
LIVE_DB  = REPO / "Data" / "SmartAgent" / "hermes_omnihorizon_v2.db"

# Default source: the main run DB (default tag when --tag is not specified).
# Q1-Q3 2024 was cold-start burn-in; the final weights in this DB reflect
# learning from the productive Q4-2024 onwards period.
# Override with --src to point at any other approved run DB.
DEFAULT_SRC = REPO / "Data" / "RealBackTest" / "db" / "rbt_main.db"

HORIZONS = ["intraday", "short_term", "swing", "positional"]


def read_brain_state(src_path: Path) -> dict:
    """Read the latest brain_state row per horizon from the backtest DB."""
    conn = sqlite3.connect(str(src_path))
    rows = {}
    for h in HORIZONS:
        row = conn.execute(
            "SELECT weights_json, p_star, total_trades, wins, calib_a, calib_b "
            "FROM brain_state WHERE horizon=? ORDER BY id DESC LIMIT 1", (h,)
        ).fetchone()
        if row:
            rows[h] = {
                "weights": json.loads(row[0]),
                "p_star": row[1],
                "total_trades": row[2],
                "wins": row[3],
                "calib_a": row[4],
                "calib_b": row[5],
            }
    conn.close()
    return rows


def print_summary(rows: dict, src: Path):
    """Print brain state for manual inspection / approval."""
    print()
    print("=" * 65)
    print(f"  SOURCE  : {src}")
    print(f"  TARGET  : {LIVE_DB}")
    print("=" * 65)
    for h, s in rows.items():
        hit_rate = (s["wins"] / s["total_trades"] * 100
                    if s["total_trades"] else 0)
        print(f"\n  [{h}]")
        print(f"    p*            : {s['p_star']:.4f}")
        print(f"    trades / wins : {s['total_trades']} / {s['wins']}"
              f"  ({hit_rate:.1f}% hit rate)")
        print(f"    calib (a, b)  : {s['calib_a']:.4f}, {s['calib_b']:.4f}")
        # top 3 weights by absolute value
        w = s["weights"]
        top = sorted(w.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
        print(f"    top features  : "
              + "  ".join(f"{k}={v:+.3f}" for k, v in top))
    print()


def seed_live(rows: dict, live_path: Path):
    """Write brain state rows into the live DB."""
    live_path.parent.mkdir(parents=True, exist_ok=True)

    # Import allstrategy to initialise the live DB schema (creates tables
    # if they don't exist yet — safe to call on an existing DB too).
    sys.path.insert(0, str(REPO / "SmartAgent"))
    import allstrategy as S
    S.DB_PATH = str(live_path)
    S.init_database()

    conn = sqlite3.connect(str(live_path))
    ts = datetime.now().isoformat()
    for h, s in rows.items():
        conn.execute(
            "INSERT INTO brain_state "
            "(horizon, timestamp, weights_json, p_star, total_trades, wins, "
            " calib_a, calib_b) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (h, ts, json.dumps(s["weights"]), s["p_star"],
             s["total_trades"], s["wins"], s["calib_a"], s["calib_b"])
        )
        print(f"  [SEEDED] {h}  p*={s['p_star']:.4f}  "
              f"trades={s['total_trades']}")
    conn.commit()
    conn.close()
    print(f"\nLive brain seeded → {live_path}")
    print("Start allstrategy.py — it will load these weights on first run.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(DEFAULT_SRC),
                    help="Backtest run DB to seed from "
                         f"(default: {DEFAULT_SRC.name})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print brain state and exit — do NOT write to live DB")
    a = ap.parse_args()

    src = Path(a.src)
    if not src.exists():
        print(f"ERROR: source DB not found: {src}")
        print()
        print("Run the seed-period backtest first:")
        print("  cd ../RealBackTest")
        print("  python3 realbacktest.py run \\")
        print("      --start 2024-10-01 --end 2026-03-31 \\")
        print("      --tag seed_brain --no-ablation")
        sys.exit(1)

    rows = read_brain_state(src)
    if not rows:
        print(f"ERROR: no brain_state rows found in {src}")
        sys.exit(1)

    missing = [h for h in HORIZONS if h not in rows]
    if missing:
        print(f"WARNING: no brain state found for horizons: {missing}")
        print("These horizons will start cold in the live agent.")

    print_summary(rows, src)

    if a.dry_run:
        print("DRY RUN — live DB not modified.")
        return

    if LIVE_DB.exists():
        print(f"WARNING: live DB already exists at {LIVE_DB}")
        answer = input("Overwrite / append seed rows? [yes/no]: ").strip().lower()
        if answer != "yes":
            print("Aborted.")
            sys.exit(0)

    seed_live(rows, LIVE_DB)


if __name__ == "__main__":
    main()
