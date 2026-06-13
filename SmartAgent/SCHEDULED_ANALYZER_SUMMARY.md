# Scheduled Analyzer Setup Complete ✅

You now have **automated daily post-market reports** with intelligent alert filtering.

---

## What's New

### 1. Analyzer Scheduler Script
**File**: `sniper_analyzer_scheduler.py`

**Features:**
- ✅ Validates trading day (skips weekends/NSE holidays)
- ✅ Checks if log exists (agent actually ran)
- ✅ Runs analyzer and captures output
- ✅ Filters alerts by configurable thresholds
- ✅ Saves report to `reports/` directory
- ✅ Auto-archives reports > 30 days old
- ✅ Fast (<2 seconds)

### 2. Scheduled Task
**Task ID**: `sniper-agent-daily-analyzer`  
**Schedule**: 3:35 PM Mon–Fri (every trading day)  
**Status**: 🟢 Active and ready

### 3. Documentation
- `SCHEDULER_USAGE.md` — Complete usage guide
- Alert threshold explanations
- Troubleshooting guide
- Customization examples

---

## Daily Workflow

### ✅ What Happens Automatically

**3:35 PM (15:35) on trading days:**
1. Scheduler validates it's a trading day
2. Checks if agent log exists
3. Runs analyzer
4. Filters alerts (shows only critical ones)
5. Saves report to `reports/sniper_report_YYYYMMDD.txt`
6. Auto-archives old reports (> 30 days)

**Result:** You get a clean daily summary without noise.

### You Can Also

**Run manually anytime:**
```bash
python3 sniper_analyzer_scheduler.py
```

**Check specific date:**
```bash
python3 sniper_analyzer_scheduler.py --date 20260611
```

**Tune alert thresholds:**
Edit `ALERT_THRESHOLDS` in `sniper_analyzer_scheduler.py` if you want more/fewer alerts.

---

## Alert Thresholds (Defaults)

These show only concerning patterns, ignore normal variation:

| Alert | Threshold | If Alert Fires |
|-------|-----------|---|
| `calib_a` drift | > 0.3 | Confidence scaling changing too fast |
| `calib_b` drift | > 0.5 | Baseline bias shifting noticeably |
| `p*` threshold | ≤ 0.56 or ≥ 0.79 | Entry bar stuck at extreme |
| Gate rejection | > 70% | Most entries rejected |

**Why these values?** They reduce false positives while catching real issues.

**Tune if needed:**
- **Lower thresholds** (e.g., 0.2 instead of 0.3) = More alerts, earlier warnings
- **Higher thresholds** (e.g., 0.4 instead of 0.3) = Fewer alerts, less noise

---

## Report Location

```
SmartAgent/
└── reports/
    ├── sniper_report_20260612.txt  ← Today's report
    ├── sniper_report_20260611.txt  ← Yesterday's report
    └── archive/
        └── sniper_report_20260511.txt  ← Reports > 30 days old
```

View today's report:
```bash
cat reports/sniper_report_$(date +%Y%m%d).txt
```

---

## Example Report Output

```
📊 OVERALL SUMMARY
────────────────────────────────────────────────────────────────────────────
  Total Trades:     12
  Wins/Losses:      8W / 4L (67% win rate)
  Total P&L:        +₹45.2K

📈 PER-HORIZON BREAKDOWN
  SHORT_TERM:  5 trades, +₹28.5K
  SWING:       4 trades, +₹18.7K
  POSITIONAL:  3 trades, -₹2.0K

🚨 ALERTS & DIAGNOSTICS (filtered by threshold)
  (No critical alerts; all patterns within normal ranges)
```

---

## Comparison: Manual vs. Scheduled Analyzer

| Aspect | Manual | Scheduled |
|--------|--------|-----------|
| **Effort** | Run manually | Automatic |
| **Consistency** | You remember | Every trading day |
| **Reports** | Only when you run | Daily archive |
| **Trending** | Manual | Automatic historical |
| **Alerts** | Manual review | Filtered automatically |
| **Disk usage** | Minimal | ~500 KB/year |

---

## Architecture

Your setup now has:

```
9:15 AM
  ↓
[sniper-agent-market-hours] ← 9:15 AM Task (Market Hours)
  ↓
  Agent runs 9:15 AM–3:30 PM
  Learns continuously when trades close
  ↓
3:30 PM
  ↓
(Market Close)
  ↓
3:35 PM
  ↓
[sniper-agent-daily-analyzer] ← 3:35 PM Task (This Session)
  ↓
  Analyzer reads log
  Filters alerts
  Saves report
  ↓
reports/sniper_report_20260612.txt ← Daily Report

Learning unaffected! Analyzer is read-only, runs after trading ends.
```

---

## FAQ

**Q: Does the scheduler affect learning?**  
A: No. Learning happens during market hours (9:15 AM–3:30 PM). Scheduler runs after and only reads the log.

**Q: What if Cowork closes?**  
A: Task runs on next launch. If closed at 3:35 PM, it runs when you reopen the app.

**Q: Can I turn it off?**  
A: Yes. Go to Cowork → Scheduled → Delete `sniper-agent-daily-analyzer`.

**Q: Too many alerts?**  
A: Increase thresholds in `ALERT_THRESHOLDS`. Higher = fewer alerts.

**Q: Not enough alerts?**  
A: Decrease thresholds. Lower = more alerts, earlier warnings.

**Q: Can I run it manually before 3:35 PM?**  
A: Yes: `python3 sniper_analyzer_scheduler.py`. Useful for testing.

**Q: What if no trades happened today?**  
A: Scheduler skips gracefully. No report generated if no log file.

---

## Files Created

| File | Purpose |
|------|---------|
| `sniper_analyzer_scheduler.py` | Scheduled wrapper (new) |
| `SCHEDULER_USAGE.md` | Complete documentation |
| `SCHEDULED_ANALYZER_SUMMARY.md` | This file |
| `reports/` | Auto-created for reports |
| `reports/archive/` | Auto-created for old reports |

---

## Next Steps

✅ **Setup complete!** No action needed right now.

1. **First report**: Tomorrow at 3:35 PM
2. **View it**: Check `reports/sniper_report_YYYYMMDD.txt`
3. **Tune alerts** (optional): Edit thresholds if too noisy
4. **Monitor learning**: Watch weight evolution, calibration drift over days/weeks

---

## Support

**Full documentation**: See `SCHEDULER_USAGE.md`
- Usage examples
- Alert interpretation
- Customization guide
- Troubleshooting

**Questions?** Check the markdown files in SmartAgent/:
- `SNIPER_AGENT_SETUP.md` — Overall setup
- `QUICK_START.md` — Quick reference
- `ANALYZER_USAGE.md` — Base analyzer
- `SCHEDULER_USAGE.md` — Scheduler details
- `SCHEDULED_ANALYZER_SUMMARY.md` — This file

---

## Status Summary

✅ **Market hours task** (9:15 AM): Active, trading live  
✅ **Analyzer scheduler** (3:35 PM): Active, generates daily reports  
✅ **Alert filtering**: Configured with smart thresholds  
✅ **Auto-archiving**: Reports > 30 days moved to archive  
✅ **Documentation**: Complete with examples  

**You're all set!** 🚀

The agent trades from 9:15 AM–3:30 PM, learns continuously.  
The scheduler generates daily reports at 3:35 PM with intelligent alerts.  
You review the report whenever you want (next morning is fine).

Enjoy your autonomous trading agent!
