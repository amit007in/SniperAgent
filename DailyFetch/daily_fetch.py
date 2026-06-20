#!/usr/bin/env python3
"""
DailyFetch — post-market incremental update for marketdata.db
=============================================================

Runs after NSE market close (4 pm IST) on trading days.
Fetches only equity bars (1-min, 30-min, daily, weekly) for all 100 symbols
in the universe — the options plane is NOT re-fetched here because historical
expired-contract data is a one-time backfill; the live agent reads the options
chain directly from the Upstox API at runtime.

The fetch is INCREMENTAL: RealBackTest/rbt/upstox_data.py tracks every
chunk it has already downloaded in the fetch_log table, so only today's
new candles are pulled (typically < 1 min for 100 equity symbols).

Usage (manual):
    cd /path/to/SniperAgent/DailyFetch
    export UPSTOX_ACCESS_TOKEN='<your_daily_token>'
    python3 daily_fetch.py

Token note:
    Upstox access tokens expire at midnight. You must export a fresh token
    before running, or store it in ~/.sniper_token and this script will read
    it from there as a fallback.

Output:
    Data/DailyFetch/logs/fetch_YYYY-MM-DD.log  — per-run structured log
    Data.nosync/marketdata.db                  — updated in-place
"""
import os
import sys
import logging
import subprocess
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE     = Path(__file__).resolve().parent          # DailyFetch/
REPO     = HERE.parent                              # SniperAgent/
sys.path.insert(0, str(REPO))
from shared_data import market_data_db, market_data_dir  # noqa: E402
RBT_DIR  = REPO / "RealBackTest"                   # realbacktest.py lives here
LOG_DIR  = REPO / "Data" / "DailyFetch" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# NSE Holidays — keep in sync with PriceActionAgent/config.py
# ---------------------------------------------------------------------------
NSE_HOLIDAYS = {
    # 2025
    "2025-01-26", "2025-02-26", "2025-03-14", "2025-03-31",
    "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01",
    "2025-08-15", "2025-08-27", "2025-10-02", "2025-10-20",
    "2025-10-21", "2025-11-05", "2025-12-25",
    # 2026
    "2026-01-26", "2026-02-26", "2026-03-20", "2026-04-02",
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-08-15",
    "2026-10-02", "2026-11-05", "2026-12-25",
}


def is_trading_day(d: date) -> bool:
    """Mon–Fri and not an NSE holiday."""
    if d.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    return d.isoformat() not in NSE_HOLIDAYS


def resolve_token() -> str:
    """
    Token priority:
      1. UPSTOX_ACCESS_TOKEN env var (set by the user / scheduler)
      2. ~/.sniper_token file (fallback for manual runs without export)
    Exits with a clear message if neither is available.
    """
    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip().strip("'\"")
    if token and token != "YOUR_UPSTOX_ACCESS_TOKEN":
        return token

    token_file = Path.home() / ".sniper_token"
    if token_file.exists():
        token = token_file.read_text().strip().strip("'\"")
        if token:
            logging.info(f"Token read from {token_file}")
            return token

    logging.error(
        "No Upstox access token found.\n"
        "  Set:  export UPSTOX_ACCESS_TOKEN='<token>'\n"
        "  Or:   echo '<token>' > ~/.sniper_token"
    )
    sys.exit(1)


def setup_logging(today_str: str) -> Path:
    log_file = LOG_DIR / f"fetch_{today_str}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )
    return log_file


def run_fetch(token: str) -> int:
    """
    Invoke realbacktest.py fetch --equity-only as a subprocess so it inherits
    the full rbt environment (sys.path, config, rate limiter, etc.).
    Returns the exit code.
    """
    env = os.environ.copy()
    env["UPSTOX_ACCESS_TOKEN"] = token
    env["RBT_DATA_DIR"] = str(market_data_dir())

    cmd = [sys.executable, str(RBT_DIR / "realbacktest.py"),
           "fetch", "--equity-only"]
    logging.info(f"Running: {' '.join(cmd)}")
    logging.info(f"Working dir: {RBT_DIR}")

    result = subprocess.run(
        cmd,
        cwd=str(RBT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Echo subprocess output line by line into our logger
    for line in result.stdout.splitlines():
        logging.info(f"  [rbt] {line}")

    return result.returncode


def main() -> int:
    now_ist = datetime.now(IST)
    today   = now_ist.date()
    today_str = today.isoformat()

    log_file = setup_logging(today_str)

    logging.info("=" * 60)
    logging.info(f"DailyFetch  {today_str}  {now_ist.strftime('%H:%M IST')}")
    logging.info(f"Log file  : {log_file}")
    logging.info(f"marketdata: {market_data_db()}")
    logging.info("=" * 60)

    # --- trading day gate ---------------------------------------------------
    if not is_trading_day(today):
        reason = "weekend" if today.weekday() >= 5 else "NSE holiday"
        logging.info(f"Not a trading day ({reason}) — nothing to fetch.")
        return 0

    # --- token --------------------------------------------------------------
    token = resolve_token()
    logging.info("Token resolved (first 6 chars): " + token[:6] + "…")

    # --- fetch --------------------------------------------------------------
    rc = run_fetch(token)
    if rc == 0:
        logging.info("Fetch completed successfully.")
    else:
        logging.error(f"Fetch exited with code {rc} — check [rbt] lines above.")

    return rc


if __name__ == "__main__":
    sys.exit(main())
