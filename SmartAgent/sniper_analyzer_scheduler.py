#!/usr/bin/env python3
"""
Sniper Analyzer Scheduler — Automated Daily Report with Smart Filtering
=========================================================================

This wrapper:
1. Validates it's a trading day (weekday, not NSE holiday)
2. Checks if agent log exists (agent actually ran)
3. Runs analyzer and saves report
4. Filters alerts by configurable thresholds
5. Auto-archives old reports (> 30 days)
6. Gracefully skips non-trading days

Designed for scheduled execution at 3:35 PM IST (market close + 5 min buffer).

Usage:
  python3 sniper_analyzer_scheduler.py              # Run analyzer today
  python3 sniper_analyzer_scheduler.py --date 20260612
  python3 sniper_analyzer_scheduler.py --check      # Check without archiving
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path
from datetime import datetime, timedelta
import logging
import json

# ============================================================================
# Configuration
# ============================================================================

WORKSPACE_DIR = Path(__file__).parent
_DATA_DIR  = WORKSPACE_DIR.parent / "Data" / "SmartAgent"
LOG_DIR    = _DATA_DIR / "logs"
REPORT_DIR = _DATA_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

ARCHIVE_SUBDIR = REPORT_DIR / "archive"
ARCHIVE_SUBDIR.mkdir(parents=True, exist_ok=True)

# Alert thresholds (tune these to reduce false positives)
ALERT_THRESHOLDS = {
    "calib_a_drift": 0.3,           # Alert if |delta calib_a| > 0.3
    "calib_b_drift": 0.5,           # Alert if |delta calib_b| > 0.5
    "p_star_min": 0.56,             # Alert if p* <= 0.56 (too loose)
    "p_star_max": 0.79,             # Alert if p* >= 0.79 (too strict)
    "gate_rejection_pct": 70,       # Alert if > 70% rejection rate
    "calibration_range_a": (0.7, 1.3),  # Alert if calib_a range outside this
    "calibration_range_b": (-0.5, 0.5), # Alert if calib_b range outside this
}

# NSE 2026 Market Holidays
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

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ============================================================================
# Helper Functions
# ============================================================================

def get_ist_now():
    """Return current time in IST."""
    import pytz
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist)

def is_trading_day(date_str=None):
    """Check if given date (or today) is a trading day."""
    if date_str:
        dt = datetime.strptime(date_str, "%Y%m%d")
    else:
        dt = get_ist_now()

    # Check if weekend
    if dt.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False, f"{dt.strftime('%Y-%m-%d')} is {dt.strftime('%A')}"

    # Check if NSE holiday
    date_only = dt.strftime("%Y-%m-%d")
    if date_only in NSE_HOLIDAYS_2026:
        return False, f"{date_only} is NSE holiday"

    return True, "Trading day"

def log_file_exists(date_str):
    """Check if log file exists for given date."""
    log_path = LOG_DIR / f"sniper_{date_str}.log"
    return log_path.exists(), log_path

def run_analyzer(date_str):
    """Execute the analyzer for given date."""
    logger.info(f"Running analyzer for {date_str}...")

    try:
        result = subprocess.run(
            [sys.executable, str(WORKSPACE_DIR / "sniper_agent_analyzer.py"), date_str],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            logger.error(f"Analyzer exited with code {result.returncode}")
            logger.error(f"stderr: {result.stderr}")
            return None

        return result.stdout

    except subprocess.TimeoutExpired:
        logger.error("Analyzer timed out (>10s)")
        return None
    except Exception as e:
        logger.error(f"Failed to run analyzer: {e}")
        return None

def filter_alerts(report_text, thresholds):
    """
    Filter alert section based on thresholds.
    Removes alerts that don't exceed threshold, keeps only critical ones.
    """
    lines = report_text.split('\n')
    filtered_lines = []
    in_alerts = False
    kept_alerts = []

    for line in lines:
        if '🚨 ALERTS & DIAGNOSTICS' in line:
            in_alerts = True

        if in_alerts:
            # Parse and filter alerts
            if '⚠️  ' in line and ':' in line:
                # Extract horizon and metric
                if 'calib_a' in line and 'drifted' in line:
                    # Extract drift value
                    try:
                        drift_str = line.split('drifted')[1].split('(')[0].strip()
                        drift = float(drift_str)
                        if abs(drift) > thresholds['calib_a_drift']:
                            kept_alerts.append(line)
                    except:
                        kept_alerts.append(line)

                elif 'calib_b' in line and 'drifted' in line:
                    try:
                        drift_str = line.split('drifted')[1].split('(')[0].strip()
                        drift = float(drift_str)
                        if abs(drift) > thresholds['calib_b_drift']:
                            kept_alerts.append(line)
                    except:
                        kept_alerts.append(line)

                elif 'p*' in line and 'at' in line:
                    # Extract p* value
                    try:
                        p_val_str = line.split('at')[1].split()[0]
                        p_val = float(p_val_str)
                        if p_val <= thresholds['p_star_min'] or p_val >= thresholds['p_star_max']:
                            kept_alerts.append(line)
                    except:
                        kept_alerts.append(line)

                elif 'gate rejection' in line:
                    try:
                        pct_str = line.split()[2].rstrip('%')
                        pct = float(pct_str)
                        if pct > thresholds['gate_rejection_pct']:
                            kept_alerts.append(line)
                    except:
                        kept_alerts.append(line)

            elif line.strip() == '' and kept_alerts:
                # End of alerts section
                in_alerts = False
                if kept_alerts:
                    filtered_lines.append('🚨 ALERTS & DIAGNOSTICS (filtered by threshold)')
                    filtered_lines.append('-' * 80)
                    filtered_lines.extend(kept_alerts)
                    filtered_lines.append('')
                continue

        if not in_alerts:
            filtered_lines.append(line)

    return '\n'.join(filtered_lines)

def save_report(report_text, date_str):
    """Save report to file with timestamp."""
    report_file = REPORT_DIR / f"sniper_report_{date_str}.txt"

    # Add metadata header
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"Generated: {now}\nAlert Thresholds: {json.dumps(ALERT_THRESHOLDS, indent=2)}\n\n"

    with open(report_file, 'w') as f:
        f.write(header)
        f.write(report_text)

    logger.info(f"Report saved: {report_file}")
    return report_file

def archive_old_reports(days=30):
    """Move reports older than N days to archive subdirectory."""
    cutoff = datetime.now() - timedelta(days=days)
    archived_count = 0

    for report_file in REPORT_DIR.glob("sniper_report_*.txt"):
        if report_file.is_file():
            mtime = datetime.fromtimestamp(report_file.stat().st_mtime)
            if mtime < cutoff:
                try:
                    shutil.move(str(report_file), str(ARCHIVE_SUBDIR / report_file.name))
                    archived_count += 1
                except Exception as e:
                    logger.warning(f"Failed to archive {report_file.name}: {e}")

    if archived_count > 0:
        logger.info(f"Archived {archived_count} old report(s) (> {days} days)")

def print_summary(report_text):
    """Print the report to stdout."""
    print(report_text)

# ============================================================================
# Main
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyzer Scheduler — Daily automated report with filtering"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Analyze specific date (YYYYMMDD), default: today"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check without archiving old reports"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip trading day validation (for testing)"
    )
    parser.add_argument(
        "--thresholds",
        type=json.loads,
        default=ALERT_THRESHOLDS,
        help="Override alert thresholds (JSON dict)"
    )

    args = parser.parse_args()

    # Determine date
    if args.date:
        date_str = args.date
    else:
        date_str = datetime.now().strftime("%Y%m%d")

    logger.info(f"Analyzer Scheduler — {date_str}")
    logger.info("=" * 80)

    # Validate trading day
    if not args.force:
        is_trading, reason = is_trading_day(date_str)
        if not is_trading:
            logger.info(f"Skipping: {reason}")
            return 0

    logger.info(f"✓ Trading day validated")

    # Check log exists
    log_exists, log_path = log_file_exists(date_str)
    if not log_exists:
        logger.info(f"No log file found: {log_path}")
        logger.info("Agent did not run or log file missing.")
        return 0

    logger.info(f"✓ Log file found: {log_path}")

    # Run analyzer
    report_text = run_analyzer(date_str)
    if not report_text:
        logger.error("Analyzer failed")
        return 1

    logger.info("✓ Analyzer completed")

    # Filter alerts by threshold
    logger.info(f"Filtering alerts by thresholds: calib_a_drift={args.thresholds['calib_a_drift']}, "
                f"calib_b_drift={args.thresholds['calib_b_drift']}, "
                f"p_star=[{args.thresholds['p_star_min']}, {args.thresholds['p_star_max']}], "
                f"gate_rejection_pct={args.thresholds['gate_rejection_pct']}")
    report_filtered = filter_alerts(report_text, args.thresholds)

    # Save report
    save_report(report_filtered, date_str)

    # Archive old reports
    if not args.check:
        archive_old_reports(days=30)

    # Print to stdout
    print_summary(report_filtered)

    logger.info("=" * 80)
    logger.info("✓ Complete")

    return 0

if __name__ == "__main__":
    sys.exit(main())
