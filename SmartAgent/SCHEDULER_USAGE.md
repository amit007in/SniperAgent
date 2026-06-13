# Sniper Analyzer Scheduler — Daily Automated Reports

Scheduled analyzer that runs automatically at **3:35 PM IST** on trading days, generates a report, filters alerts intelligently, and archives old reports.

## What It Does

✅ **Validates trading day** — Skips weekends and NSE holidays  
✅ **Checks log exists** — Only runs if agent actually traded  
✅ **Generates report** — Runs analyzer and captures output  
✅ **Filters alerts** — Shows only concerning patterns (tunable thresholds)  
✅ **Saves report** — Stores to `reports/sniper_report_YYYYMMDD.txt`  
✅ **Auto-archives** — Moves reports > 30 days old to `reports/archive/`  
✅ **Fast & lightweight** — Completes in < 2 seconds  

## Schedule

| Day | Time | Task |
|-----|------|------|
| Mon–Fri | 3:35 PM IST | Run analyzer automatically |
| Saturday–Sunday | — | Skipped (no trading) |
| NSE Holidays | — | Skipped automatically |

**Why 3:35 PM?** Market closes at 3:30 PM; 5-minute buffer for data to settle.

---

## Alert Thresholds

The scheduler filters alerts to reduce noise. Only shows patterns that exceed these thresholds:

| Alert | Threshold | Meaning |
|-------|-----------|---------|
| `calib_a` drift | > 0.3 | Confidence scaling changing too fast |
| `calib_b` drift | > 0.5 | Baseline bias shifting noticeably |
| `p*` threshold | ≤ 0.56 or ≥ 0.79 | Entry bar at extremes (stuck) |
| Gate rejection rate | > 70% | Most entries being rejected |

**Default values minimize false positives** while catching real issues.

### Tune Alert Thresholds

Edit `ALERT_THRESHOLDS` in `sniper_analyzer_scheduler.py`:

```python
ALERT_THRESHOLDS = {
    "calib_a_drift": 0.3,           # Make 0.2 for more sensitive
    "calib_b_drift": 0.5,           # Make 0.3 for more sensitive
    "p_star_min": 0.56,             # Min p* before alert
    "p_star_max": 0.79,             # Max p* before alert
    "gate_rejection_pct": 70,       # Alert if > 70% rejected
}
```

**Lower thresholds** = More alerts (more noise, earlier warnings)  
**Higher thresholds** = Fewer alerts (less noise, later detection)

---

## Usage

### Automatic (Scheduled)
No action needed! Runs at **3:35 PM Mon–Fri** automatically.
- Report saved to `reports/sniper_report_20260612.txt`
- Old reports archived automatically

### Manual Check (Anytime)

Run analyzer immediately without waiting:

```bash
cd /Users/amitkumar/Personal/work/source\ code/SniperAgent/SmartAgent

# Run today's analysis
python3 sniper_analyzer_scheduler.py

# Run specific date
python3 sniper_analyzer_scheduler.py --date 20260611

# Check without archiving old reports
python3 sniper_analyzer_scheduler.py --check

# Skip trading day validation (for testing)
python3 sniper_analyzer_scheduler.py --force
```

---

## Output Example

```
================================================================================
  SNIPER AGENT — POST-MARKET ANALYSIS
  Date: 20260612
================================================================================

📊 OVERALL SUMMARY
────────────────────────────────────────────────────────────────────────────────
  Total Trades:     4
  Wins/Losses:      2W / 2L (50.0% win rate)
  Total P&L:        +₹10.1K

📈 PER-HORIZON BREAKDOWN
────────────────────────────────────────────────────────────────────────────────

  SHORT_TERM
  ────────────────────────────────────────────────────────────────────────────
    Trades:        1 total (1W / 0L, 100% win rate)
    P&L:           +₹5.7K
    R-multiple:    avg +1.89R, best +1.89R, worst +1.89R
    Learning:      2 trade(s) closed
    p*-threshold:  0.680 → 0.670 (📈-0.010)
    ✓ calib_a:     1.02 → 1.01 (-0.01, range [1.01, 1.02])
    ✓ calib_b:     -0.15 → -0.12 (+0.03, range [-0.15, -0.12])
    Gate rejections: 1 entries rejected (iv_cap: 1)

  SWING
  ────────────────────────────────────────────────────────────────────────────
    Trades:        2 total (1W / 1L, 50% win rate)
    P&L:           +₹5.3K
    Learning:      2 trade(s) closed
    p*-threshold:  0.720 → 0.690 (📈-0.030)
    ✓ calib_a:     0.98 → 0.99 (+0.01, range [0.98, 0.99])
    Gate rejections: 1 entries rejected (cost_gate: 1)

================================================================================
  Report generated: 2026-06-11 20:26:33
================================================================================

🚨 ALERTS & DIAGNOSTICS (filtered by threshold)
────────────────────────────────────────────────────────────────────────────────
  (No critical alerts; all patterns within normal ranges)
```

---

## File Structure

```
SmartAgent/
├── sniper_agent_analyzer.py          # Base analyzer (no filtering)
├── sniper_analyzer_scheduler.py      # Scheduled wrapper (new)
├── ANALYZER_USAGE.md                 # Analyzer documentation
├── SCHEDULER_USAGE.md                # This file
│
├── logs/
│   └── sniper_20260612.log          # Daily trading log (auto-generated)
│
└── reports/                          # Reports saved here (auto-created)
    ├── sniper_report_20260612.txt   # Today's report
    ├── sniper_report_20260611.txt   # Yesterday's report
    └── archive/
        ├── sniper_report_20260511.txt  # Reports > 30 days old
        └── sniper_report_20260510.txt
```

---

## Scenario Examples

### Scenario 1: Normal Trading Day
**3:35 PM runs automatically:**
```
✓ Trading day validated
✓ Log file found
✓ Analyzer completed
✓ Report saved to reports/sniper_report_20260612.txt
✓ No old reports archived
(No alerts; all metrics normal)
```

### Scenario 2: Weekend / NSE Holiday
**Task skips gracefully:**
```
Skipping: 2026-06-13 is Saturday
```

### Scenario 3: Agent Didn't Run
**Task skips if no log:**
```
✓ Trading day validated
No log file found: logs/sniper_20260612.log
Agent did not run or log file missing.
```

### Scenario 4: Critical Alert Detected
**Threshold exceeded; alert shown:**
```
🚨 ALERTS & DIAGNOSTICS (filtered by threshold)
────────────────────────────────────────────────────────────────────────────────
  ⚠️  short_term: calib_a drifted -0.35 (overconfidence tuning away)
  ⚠️  positional: p* threshold at 0.80 (at max; too many losses)
  ⚠️  swing: 75% gate rejection rate (7 rejected, 2 entered)
```

---

## Report Files

### Location
`/Users/amitkumar/Personal/work/source code/SniperAgent/SmartAgent/reports/`

### Naming
`sniper_report_YYYYMMDD.txt`
- `sniper_report_20260612.txt` = June 12, 2026 report
- `sniper_report_20260611.txt` = June 11, 2026 report

### Archiving
- Reports > 30 days old automatically moved to `reports/archive/`
- Keeps main reports/ directory clean
- Archive is still accessible if you need historical data

### Viewing Reports

```bash
# Today's report
cat reports/sniper_report_$(date +%Y%m%d).txt

# Yesterday's report
cat reports/sniper_report_$(date -v-1d +%Y%m%d).txt

# All recent reports
ls -lart reports/sniper_report_*.txt | tail -10

# Archived reports
ls reports/archive/ | head -5
```

---

## Alert Interpretation Guide

### ✅ No Alerts (All Normal)
```
🚨 ALERTS & DIAGNOSTICS (filtered by threshold)
(No critical alerts; all patterns within normal ranges)
```
**Meaning:** Agent is learning normally, no concerning patterns detected.

### ⚠️ Calibration Alerts
```
⚠️  short_term: calib_a drifted -0.35 (overconfidence tuning away)
```
**Meaning:** Confidence scaling shifted quickly. Agent thinks it was overconfident.  
**Action:** Watch for next 2-3 days. Should stabilize. If continues, may indicate regime change.

```
⚠️  positional: calib_b drifted +0.55 (baseline bias shifting)
```
**Meaning:** Baseline bias (constant term) is changing.  
**Action:** Recalibration in progress. Normal after recent win/loss streak. Monitor.

### ⚠️ p* Extremes
```
⚠️  swing: p* threshold at 0.80 (near max 0.80; too many losses)
```
**Meaning:** Entry bar raised to maximum after repeated losses. Very strict now.  
**Action:** Either:
- Wait for a win to relax bar (drops 0.005 per win)
- Check if trade thesis is broken (market regime changed)
- Reduce position size to let bar relax faster

```
⚠️  positional: p* threshold at 0.55 (near min 0.55; too many wins)
```
**Meaning:** Entry bar lowered to minimum after repeated wins. Very loose now.  
**Action:** Watch for overfitting. Win streak may not continue. Bar will rise after first loss.

### ⚠️ Gate Rejection Alert
```
⚠️  swing: 82% gate rejection rate (9 rejected, 2 entered)
```
**Meaning:** Most entries rejected by gates. Only 2 out of 11 evaluations entered.  
**Action:** Check which gate is rejecting:
- `cost_gate`: Volatility low, costs bite → normal in calm markets
- `iv_cap`: Option IV too high → less attractive entries
- `cooldown`: Exiting positions → positions closing, cooldown active
- `min_profit`: Target too small for horizon → expected, not a problem

---

## Customization

### Change Alert Thresholds

Edit `sniper_analyzer_scheduler.py`:

```python
ALERT_THRESHOLDS = {
    "calib_a_drift": 0.2,  # More sensitive (was 0.3)
    "calib_b_drift": 0.3,  # More sensitive (was 0.5)
    "p_star_min": 0.60,    # Raise bar (was 0.56)
    "p_star_max": 0.75,    # Lower bar (was 0.79)
    "gate_rejection_pct": 80,  # Less sensitive (was 70)
}
```

Then restart the scheduled task or run manually.

### Add/Remove NSE Holidays

Update `NSE_HOLIDAYS_2026` in the script for future years:

```python
NSE_HOLIDAYS_2026 = {
    "2026-01-26",  # Republic Day
    "2026-03-11",  # Maha Shivaratri
    # ... add 2027 holidays here when needed
}
```

### Change Archiving Window

Default: Reports older than 30 days archived.

Edit in `main()`:
```python
archive_old_reports(days=60)  # Archive after 60 days instead
```

---

## Troubleshooting

### Scheduled Task Not Running

1. **Check if Cowork is open** — Tasks run only while app is open
2. **Verify task exists** — Go to Scheduled section in sidebar
3. **Check permissions** — Script might lack execute permissions
   ```bash
   chmod +x sniper_analyzer_scheduler.py
   ```

### Reports Not Being Saved

1. **Check reports/ directory exists** — Should be auto-created:
   ```bash
   ls -la reports/
   ```
2. **Check write permissions:**
   ```bash
   touch reports/test.txt  # Should succeed
   ```

### Too Many/Too Few Alerts

1. **Review threshold values** in script
2. **Tune as needed** (see Customization section above)
3. **Test manually** before adjusting schedule

### Analyzer Takes Too Long

- Normal: < 2 seconds
- If > 10 seconds: Log file might be huge, old logs can be deleted

---

## Integration with Market Hours Task

| Time | Task | Purpose |
|------|------|---------|
| **9:15 AM** | `sniper-agent-market-hours` | Start trading session |
| — | Agent runs continuously | Trading happens, learning occurs |
| **3:30 PM** | (Auto square-off intraday) | Market close |
| **3:35 PM** | `sniper-agent-daily-analyzer` | Generate daily report |
| **Evening+** | Manual review (optional) | Read report, assess learning |

Both tasks are independent:
- Market hours task: Trades the market
- Analyzer task: Analyzes trading results
- Neither depends on the other

---

## Performance Impact

- **Disk**: ~2-5 KB per report (100 reports/year = ~500 KB total)
- **CPU**: < 2 seconds per run
- **Memory**: Minimal (< 50 MB)
- **Network**: None (reads local files only)

**Zero impact on trading** — only runs after market close.

---

## Next Steps

1. ✅ Scheduled task created (runs at 3:35 PM Mon–Fri)
2. Review alert thresholds (tune if too noisy)
3. First report will be generated tomorrow at 3:35 PM
4. Check `reports/sniper_report_YYYYMMDD.txt` after market close

Done! 🚀
