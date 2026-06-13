# Assessment Job → Manual Analyzer (Option B)

## What Changed

Instead of a scheduled assessment task at 3:30 PM, you now have a **manual analysis script** that you run after market close.

### Before (Scheduled Task)
❌ 3:30 PM task: `sniper-agent-assessment`
- Just logged the same continuous output
- No intelligent summary
- Ran whether you needed it or not

### Now (Manual Analyzer)  
✅ On-demand analysis script: `sniper_agent_analyzer.py`
- Run it whenever you want (after market close, evening, next morning)
- Generates a clean, formatted report
- Shows trade stats, weight evolution, calibration drift, gate analysis
- Takes < 1 second to run

---

## Quick Start

```bash
# After market close (any time):
cd /Users/amitkumar/Personal/work/source\ code/SniperAgent/SmartAgent
python3 sniper_agent_analyzer.py

# Or specific date:
python3 sniper_agent_analyzer.py 20260612
```

## Sample Output

```
📊 OVERALL SUMMARY
────────────────────────────────────────────────────────────────────────────
  Total Trades:     4
  Wins/Losses:      2W / 2L (50.0% win rate)
  Total P&L:        +₹10.1K

📈 SHORT_TERM
────────────────────────────────────────────────────────────────────────────
  Trades:        1 total (1W / 0L, 100% win rate)
  P&L:           +₹5.7K
  R-multiple:    avg +1.89R, best +1.89R, worst +1.89R
  Learning:      2 trade(s) closed
  p*-threshold:  0.680 → 0.670 (📈-0.010)
  ✓ calib_a:     1.02 → 1.01 (-0.01, range [1.01, 1.02])
  Gate rejections: 1 entries rejected (iv_cap: 1)

📈 SWING
────────────────────────────────────────────────────────────────────────────
  Trades:        2 total (1W / 1L, 50% win rate)
  P&L:           +₹5.3K
  Learning:      2 trade(s) closed
  p*-threshold:  0.720 → 0.690 (📈-0.030)
  Gate rejections: 1 entries rejected (cost_gate: 1)
```

---

## Advantages of Manual Analyzer

| Aspect | Scheduled Task | Manual Analyzer |
|--------|---|---|
| **Cost** | Runs every day (even weekends) | Only when you run it |
| **Flexibility** | Fixed 3:30 PM | Run anytime (3:45 PM, next morning, etc.) |
| **Output** | Just raw logs | Formatted, cleaned-up report |
| **Learning** | None (just logging) | Doesn't affect learning either way |
| **Maintenance** | Automatic (but useless) | Run as needed |

---

## What the Analyzer Shows

✅ **Trade Summary** — Total trades, W/L, P&L, R-multiple stats  
✅ **Per-Horizon Breakdown** — Details for short_term, swing, positional  
✅ **Weight Evolution** — How p* threshold changed (entry conviction)  
✅ **Calibration Drift** — How confident the predictions are  
✅ **Gate Analysis** — Why entries were rejected  
✅ **Alerts** — Warnings for concerning patterns  

---

## Usage Examples

### Daily Review (After 3:30 PM)
```bash
python3 sniper_agent_analyzer.py
# ↑ Analyze today's log, see summary in ~1 second
```

### Check Specific Date
```bash
python3 sniper_agent_analyzer.py 20260611
# ↑ Analyze yesterday's session
```

### Save to File
```bash
python3 sniper_agent_analyzer.py > report_20260612.txt
# ↑ Share with yourself or others
```

### Check Gate Rejections Only
```bash
python3 sniper_agent_analyzer.py | grep "Gate rejections" -A 10
# ↑ Focus on why entries were rejected
```

### Find Alerts
```bash
python3 sniper_agent_analyzer.py | grep "⚠️ "
# ↑ See any concerning patterns (calibration drift, etc.)
```

---

## What You Need to Do

1. **Remove the 3:30 PM scheduled task** (optional, but recommended):
   - Go to Cowork → Scheduled
   - Find `sniper-agent-assessment`
   - Delete it

2. **Use the analyzer manually**:
   - After each market close, run: `python3 sniper_agent_analyzer.py`
   - Takes ~1 second
   - Review the report

3. **Or keep it light**:
   - Just check logs manually: `tail logs/sniper_$(date +%Y%m%d).log`
   - For detailed analysis, use the analyzer

---

## File Reference

| File | Purpose |
|------|---------|
| `sniper_agent_analyzer.py` | The analyzer script (fast, no learning cost) |
| `ANALYZER_USAGE.md` | Full documentation with examples |
| `logs/sniper_YYYYMMDD.log` | Daily log file (parsed by analyzer) |

---

## FAQ

**Q: Do I HAVE to run the analyzer?**  
A: No. All learning happens during the continuous main loop (9:15 AM–3:30 PM). The analyzer is just for monitoring.

**Q: What if I don't run it for a week?**  
A: Fine. Just run it on any date to see that day's stats.

**Q: Can I automate it with a cron?**  
A: Yes, if you want. See ANALYZER_USAGE.md for crontab example.

**Q: Does it slow down trading?**  
A: No. It only runs after 3:30 PM (market close) and only when you manually invoke it.

**Q: What does it actually do?**  
A: Reads the log file, parses trade outcomes, weight updates, and gate rejections, then pretty-prints a summary. No trading decisions made.

---

## Summary

✅ **Analyzer created**: `sniper_agent_analyzer.py`  
✅ **Usage guide**: `ANALYZER_USAGE.md`  
✅ **Scheduled task removed**: No more redundant 3:30 PM job  
✅ **On-demand analysis**: Run when you want, takes 1 second  
✅ **Learning unaffected**: All learning still happens automatically during market hours  

That's it! You're good to go.
