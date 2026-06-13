#!/usr/bin/env python3
"""
Sniper Agent Wrapper — NSE Market Hours Orchestrator
======================================================

This script:
1. Validates it's a trading day (weekday, not NSE holiday)
2. Prevents MacBook sleep during market hours
3. Manages Upstox token from environment
4. Configures horizons (disable intraday, enable short_term/swing/positional)
5. Runs allstrategy.py during 9:15 AM–3:30 PM IST
6. Executes post-market assessment after 3:30 PM
7. Ensures clean state persistence
"""

import os
import sys
import subprocess
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================================
# NSE 2026 Market Holidays (IST dates)
# ============================================================================
NSE_HOLIDAYS_2026 = {
    "2026-01-26",  # Republic Day
    "2026-03-11",  # Maha Shivaratri
    "2026-03-29",  # Good Friday
    "2026-03-30",  # Holi
    "2026-04-02",  # Eid ul-Fitr (approx)
    "2026-04-14",  # Ambedkar Jayanti
    "2026-05-01",  # May Day
    "2026-08-15",  # Independence Day
    "2026-08-27",  # Janmashtami
    "2026-09-30",  # Mahatma Gandhi Jayanti
    "2026-10-02",  # Gandhi Jayanti
    "2026-10-25",  # Diwali (Lakshmi Puja)
    "2026-10-26",  # Diwali (day after)
    "2026-11-12",  # Guru Nanak Jayanti
    "2026-12-25",  # Christmas
}

# ============================================================================
# Configuration
# ============================================================================
WORKSPACE_DIR = Path(__file__).parent
_DATA_DIR = WORKSPACE_DIR.parent / "Data" / "SmartAgent"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
BRAIN_STATE_FILE = _DATA_DIR / ".hermes_brain_state.json"
LOG_DIR = _DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f"sniper_{datetime.now().strftime('%Y%m%d')}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

IST_OFFSET = 5.5  # UTC+5:30
MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:30"
ASSESSMENT_TIME = "15:35"  # 5 minutes after close

# ============================================================================
# Helper Functions
# ============================================================================

def get_ist_now():
    """Return current time in IST."""
    import pytz
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist)

def is_trading_day():
    """Check if today is a valid NSE trading day."""
    today = get_ist_now()
    today_str = today.strftime("%Y-%m-%d")

    # Check if weekend
    if today.weekday() >= 5:  # Saturday = 5, Sunday = 6
        logger.info(f"Weekend ({today.strftime('%A')}), skipping.")
        return False

    # Check if NSE holiday
    if today_str in NSE_HOLIDAYS_2026:
        logger.info(f"NSE holiday on {today_str}, skipping.")
        return False

    logger.info(f"Trading day validated: {today.strftime('%Y-%m-%d %A')}")
    return True

def is_market_hours():
    """Check if current time is within market hours (9:15 AM – 3:30 PM IST)."""
    now = get_ist_now()
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)

    return market_start <= now <= market_end

def prevent_sleep(duration_minutes=60):
    """Prevent MacBook sleep using caffeinate for given duration."""
    try:
        # caffeinate -i: prevent idle sleep
        # -t: duration in seconds
        seconds = duration_minutes * 60
        subprocess.Popen(
            ["caffeinate", "-i", "-t", str(seconds)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logger.info(f"MacBook sleep prevention enabled for {duration_minutes} minutes")
    except FileNotFoundError:
        logger.warning("caffeinate not found; MacBook may sleep during trading hours")
    except Exception as e:
        logger.warning(f"Failed to prevent sleep: {e}")

def validate_upstox_token():
    """Validate that UPSTOX_ACCESS_TOKEN is set."""
    token = os.environ.get("UPSTOX_ACCESS_TOKEN")
    if not token:
        logger.error("UPSTOX_ACCESS_TOKEN not set in environment. Cannot proceed.")
        logger.error("Set manually with: export UPSTOX_ACCESS_TOKEN='your_token'")
        return False

    if len(token) < 10:
        logger.error("UPSTOX_ACCESS_TOKEN appears invalid (too short)")
        return False

    logger.info("Upstox token validated")
    return True

def configure_horizons(disable_intraday=True):
    """
    Create a configuration dict that disables intraday horizon.
    This will be passed as environment or written to config override.
    """
    config = {
        "HORIZON_ENABLED": {
            "intraday": not disable_intraday,
            "short_term": True,
            "swing": True,
            "positional": True,
        }
    }
    logger.info(f"Horizon config: {config['HORIZON_ENABLED']}")
    return config

def run_allstrategy(mode="market-hours"):
    """
    Execute allstrategy.py with proper configuration.

    Args:
        mode: 'market-hours' (continuous trading) or 'assessment' (post-market review)
    """
    logger.info(f"Starting allstrategy.py in {mode} mode")

    env = os.environ.copy()

    # Ensure Upstox token is set
    if not validate_upstox_token():
        return False

    # Set horizon configuration
    config = configure_horizons(disable_intraday=True)
    env["HERMES_HORIZON_CONFIG"] = json.dumps(config)

    # Set mode
    env["HERMES_MODE"] = mode
    env["HERMES_ASSESSMENT"] = "true" if mode == "assessment" else "false"

    try:
        # Run allstrategy.py
        result = subprocess.run(
            [sys.executable, str(WORKSPACE_DIR / "allstrategy.py")],
            env=env,
            capture_output=False
        )

        if result.returncode == 0:
            logger.info(f"allstrategy.py completed successfully in {mode} mode")
            return True
        else:
            logger.error(f"allstrategy.py exited with code {result.returncode}")
            return False

    except Exception as e:
        logger.error(f"Failed to run allstrategy.py: {e}")
        return False

def run_market_hours_loop():
    """
    Main market hours loop:
    1. Prevent sleep
    2. Run allstrategy.py
    3. Exit at 3:30 PM
    """
    logger.info("=" * 70)
    logger.info("SNIPER AGENT MARKET HOURS SESSION")
    logger.info("=" * 70)

    if not is_trading_day():
        logger.info("Not a trading day, exiting.")
        return False

    if not is_market_hours():
        logger.warning("Not currently in market hours, but proceeding anyway...")

    # Prevent sleep for 6+ hours (full market hours + buffer)
    prevent_sleep(duration_minutes=400)

    # Run the agent
    success = run_allstrategy(mode="market-hours")

    logger.info("=" * 70)
    logger.info("MARKET HOURS SESSION COMPLETE")
    logger.info("=" * 70)

    return success

def run_assessment():
    """
    Post-market assessment:
    1. Review closed trades
    2. Print weight evolution
    3. Generate daily report
    """
    logger.info("=" * 70)
    logger.info("POST-MARKET ASSESSMENT")
    logger.info("=" * 70)

    if not is_trading_day():
        logger.info("Not a trading day, skipping assessment.")
        return False

    success = run_allstrategy(mode="assessment")

    logger.info("=" * 70)
    logger.info("ASSESSMENT COMPLETE")
    logger.info("=" * 70)

    return success

# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Entry point for scheduled execution."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Sniper Agent Wrapper for NSE Market Hours"
    )
    parser.add_argument(
        "--mode",
        choices=["market-hours", "assessment"],
        default="market-hours",
        help="Execution mode (default: market-hours)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip holiday/weekday validation (for testing)"
    )

    args = parser.parse_args()

    if args.mode == "market-hours":
        if not args.force and not is_trading_day():
            logger.info("Skipping market hours (not a trading day)")
            return 0
        success = run_market_hours_loop()
    else:  # assessment
        if not args.force and not is_trading_day():
            logger.info("Skipping assessment (not a trading day)")
            return 0
        success = run_assessment()

    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
