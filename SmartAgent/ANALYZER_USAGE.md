# Sniper Agent Analyzer — Usage Guide

The analyzer parses your daily trading logs and generates a clean post-market summary without needing a scheduled task.

## Quick Start

```bash
# Analyze today's log
cd /Users/amitkumar/Personal/work/source\ code/SniperAgent/SmartAgent
python3 sniper_agent_analyzer.py

# Analyze a specific date
python3 sniper_agent_analyzer.py 20260612

# List available logs
ls logs/sniper_*.log
```

## Output Example

```
================================================================================
  SNIPER AGENT — POST-MARKET ANALYSIS
  Date: 20260612
================================================================================

📊 OVERALL SUMMARY
────────────────────────────────────────────────────────────────────────────────
  Total Trades:     12
  Wins/Losses:      8W / 4L (67% win rate)
  Total P&L:        +₹45.2K

📈 PER-HORIZON BREAKDOWN
────────────────────────────────────────────────────────────────────────────────

  SHORT_TERM
  ────────────────────────────────────────────────────────────────────────────
    Trades:        5 total (4W / 1L, 80% win rate)
    P&L:           +₹28.5K
    R-multiple:    avg +1.45R, best +2.56R, worst -1.23R
    Learning:      5 trade(s) closed
    p*-threshold:  0.700 → 0.685 (📈-0.015)
    ✓ calib_a:     1.00 → 1.05 (+0.05, range [0.99, 1.06])
    ✓ calib_b:     0.00 → -0.08 (-0.08, range [-0.12, 0.02])
    Gate rejections: 8 entries rejected
      • cost_gate: 4
      • iv_cap: 3
      • cooldown: 1

  SWING
  ────────────────────────────────────────────────────────────────────────────
    Trades:        4 total (3W / 1L, 75% win rate)
    P&L:           +₹18.7K
    R-multiple:    avg +1.23R, best +2.34R, worst -0.89R
    Learning:      4 trade(s) closed
    p*-threshold:  0.700 → 0.680 (📈-0.020)
    ✓ calib_a:     1.00 → 0.98 (-0.02, range [0.96, 1.02])
    ✓ calib_b:     0.00 → +0.05 (+0.05, range [-0.03, 0.15])
    Gate rejections: 12 entries rejected
      • cost_gate: 6
      • min_profit: 4
      • cooldown: 2

  POSITIONAL
  ────────────────────────────────────────────────────────────────────────────
    Trades:        3 total (1W / 2L, 33% win rate)
    P&L:           -₹2.0K
    R-multiple:    avg +0.12R, best +1.67R, worst -0.95R
    Learning:      3 trade(s) closed
    p*-threshold:  0.700 → 0.745 (📉+0.045)
    ⚠️  calib_a:    1.00 → 0.85 (-0.15, range [0.82, 1.02])
    ✓ calib_b:     0.00 → -0.12 (-0.12, range [-0.18, 0.05])
    Gate rejections: 5 entries rejected
      • cost_gate: 3
      • min_profit: 2

  INTRADAY
  ────────────────────────────────────────────────────────────────────────────
    Trades:        None

================================================================================
  Report generated: 2026-06-12 16:15:23
================================================================================

🚨 ALERTS & DIAGNOSTICS
────────────────────────────────────────────────────────────────────────────────
  ⚠️  positional: 83% gate rejection rate (5 rejected, 1 entered)
```

## Report Sections Explained

### 📊 Overall Summary
- **Total Trades**: Count across all horizons
- **Wins/Losses**: Breakdown and win rate percentage
- **Total P&L**: Net profit/loss in rupees

### 📈 Per-Horizon Breakdown

Each horizon shows:

#### Trades
- Count: `5 total (4W / 1L, 80% win rate)`
- P&L: `+₹28.5K` (total realized profit/loss)
- R-multiple: 
  - `avg`: Average risk-multiple per trade
  - `best`: Best outcome (e.g., +2.56R = 2.56× the risk as profit)
  - `worst`: Worst outcome (e.g., -1.23R = loss of 1.23× the risk)

#### Learning
- Updates count: Number of trades that closed and updated the learner
- **p*-threshold**: Entry conviction bar
  - Start → End: How it changed during the day
  - 📈 means it lowered (easier entries, more wins)
  - 📉 means it raised (stricter entries, more losses)
  - Range: [0.55, 0.80] — should stay within bounds
  
- **calib_a**: Confidence scaling (1.0 = unbiased)
  - `⚠️  ` flag if delta > 0.3 (drifting fast)
  - Range shows min/max during session
  - > 1.0: overconfident (needs restraint)
  - < 1.0: underconfident (or too conservative)
  
- **calib_b**: Baseline bias offset (0.0 = unbiased)
  - `⚠️  ` flag if delta > 0.5 (shifting fast)
  - Negative: your predicted win rates are higher than realized
  - Positive: you're predicting too many losses

#### Gate Rejections
- Total count of entry rejections
- Breakdown by reason:
  - `cost_gate`: Expected profit after costs too low
  - `iv_cap`: Call IV too high
  - `cooldown`: Still in cooldown period from last exit
  - `min_profit`: ATR-target too small for horizon
  - `kelly_zero`: Kelly fraction would be zero (no edge)
  - `min_bars`: Not enough history loaded
  - `already_open`: Position already open for this (horizon, symbol) pair

### 🚨 Alerts & Diagnostics

Automatic warnings for concerning patterns:

| Alert | Meaning | Action |
|-------|---------|--------|
| `calib_a` drifted > 0.3 | Overconfidence tuning rapidly | Watch for overfitting to recent noise |
| `calib_b` drifted > 0.5 | Baseline bias shifting | Recalibration in progress; monitor |
| `p*` at 0.79 or higher | Bar extremely strict | Too many losses; may take days to relax |
| `p*` at 0.56 or lower | Bar extremely loose | Too many wins; overfitting risk |
| Gate rejection > 70% | Most entries rejected | Check cost/IV/cooldown constraints |

---

## Symbol Key

| Icon | Meaning |
|------|---------|
| 📊 | Overall summary |
| 📈 | Per-horizon details |
| ✅ | Good (in overall summary) |
| ❌ | Loss (in trade outcome) |
| 📈 | p* lowered (easier entries) |
| 📉 | p* raised (stricter entries) |
| ✓  | Calibration drift normal |
| ⚠️  | Calibration drift concerning |
| 🚨 | Alert section |

---

## Use Cases

### Daily Review (After 3:30 PM)
```bash
python3 sniper_agent_analyzer.py
```
Quick check: Did the agent trade? What was the P&L? Any concerning drift?

### Weekly Trend Analysis
```bash
# Check last 5 trading days
for date in 20260612 20260611 20260610 20260609 20260606; do
  echo "=== $date ===" 
  python3 sniper_agent_analyzer.py $date | tail -20
done
```

### Troubleshoot Specific Date
```bash
# A day with few trades — why?
python3 sniper_agent_analyzer.py 20260605

# Look at gate rejection breakdown
# If 90% are cost_gate → volatility too low, costs bite
# If 80% are iv_cap → option IV elevated, try IV cap change
# If 70% are cooldown → too many trades, reduce positions
```

### Monitor Learning Health
```bash
# Is calibration stable?
# Check calib_a range — should be [0.8, 1.2] (rough)
# Check calib_b range — should be [-0.3, +0.3] (rough)

# Is p* reasonable?
# Start of day: 0.70 (default)
# After many wins: should drift down to ~0.65 (easier entries)
# After many losses: should drift up to ~0.75 (stricter entries)

# If p* is 0.79 or 0.55, something is wrong
```

---

## Limitations

The analyzer reads only the printed log output. It cannot:
- Parse weight evolution (printed per-trade, not aggregated)
- Show feature-by-feature credit attribution
- Correlate gate rejections with specific symbols
- Track intraday square-off timing

For deeper analysis, you'd need to:
1. Access the SQLite database directly (`~/.hermes/skills/hermes_omnihorizon_v2.db`)
2. Parse the brain state file (per-horizon weights, credits)
3. Use a separate dashboard or logging backend

---

## Tips

**Redirect to file for sharing:**
```bash
python3 sniper_agent_analyzer.py 20260612 > analysis_20260612.txt
```

**Compare two days side-by-side:**
```bash
python3 sniper_agent_analyzer.py 20260612 > day1.txt
python3 sniper_agent_analyzer.py 20260611 > day2.txt
diff day1.txt day2.txt
```

**Grep for specific patterns:**
```bash
python3 sniper_agent_analyzer.py | grep -A 5 "SHORT_TERM"
python3 sniper_agent_analyzer.py | grep "⚠️ "
python3 sniper_agent_analyzer.py | grep "Gate rejections"
```

---

## Log File Format

Analyzer expects logs in this format (generated by `sniper_agent_wrapper.py`):

```
[LEARN/horizon] R=+1.23 -> p*=0.68 | calib a=1.02 b=-0.15 | w={...}
[COST-GATE/horizon] SYMBOL: ...
[COOLDOWN/horizon] SYMBOL: ...
✅ [PROFIT/horizon] SYMBOL exit price | R=+1.89 | PnL=+5670.00
❌ [LOSS/horizon] SYMBOL exit price | R=-1.23 | PnL=-2450.00
```

If logs aren't parsing, check:
1. Log file exists in `logs/sniper_YYYYMMDD.log`
2. Allstrategy.py is printing in the format above
3. No special characters in SYMBOL names that break regex

---

## No Scheduled Task Needed!

Instead of a scheduled 3:30 PM assessment job:

**Option 1: Manual check after market close**
```bash
# Around 3:45 PM, run:
python3 sniper_agent_analyzer.py
```

**Option 2: Your own cron (if you want automation)**
```bash
# Add to crontab:
35 15 * * 1-5 cd /Users/amitkumar/Personal/work/source\ code/SniperAgent/SmartAgent && python3 sniper_agent_analyzer.py > analyzer_$(date +\%Y\%m\%d).txt
```

But manual is fine — the analyzer is fast (< 1 second).
