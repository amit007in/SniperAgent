# Sniper Agent — NSE Market Hours Orchestration Setup

## Overview

Your Sniper Agent (Hermes Omnihorizon) is now configured to run automatically during NSE market hours with the following features:

- ✅ **Market Hours**: 9:15 AM – 3:30 PM IST, weekdays only
- ✅ **Holiday Exclusion**: Automatically skips NSE holidays (Republic Day, Diwali, etc.)
- ✅ **Weekend Skip**: No trading on Saturdays/Sundays
- ✅ **MacBook Sleep Prevention**: Enabled during 9:15 AM – 3:30 PM (via `caffeinate`)
- ✅ **Intraday Horizon Disabled**: Only short_term (30-min), swing (daily), and positional (weekly) are active
- ✅ **Upstox Token**: Reads from `UPSTOX_ACCESS_TOKEN` environment variable
- ✅ **Post-Market Assessment**: Automatic review at 3:30 PM with performance report
- ✅ **Crash Safety**: All positions persisted to SQLite; survives restarts

---

## Scheduled Tasks

Two cron tasks have been created and are now active:

### 1. Market Hours Session — 9:15 AM Weekdays
**Schedule**: 9:15 AM Mon–Fri (cron: `15 9 * * 1-5`)  
**Task ID**: `sniper-agent-market-hours`  
**Location**: `/Users/amitkumar/Documents/Claude/Scheduled/sniper-agent-market-hours/SKILL.md`

**What it does:**
- Validates today is a trading day (weekday, not NSE holiday)
- Prevents MacBook sleep for ~6.5 hours
- Loads Upstox token from environment
- Disables intraday horizon; enables short_term, swing, positional
- Runs `allstrategy.py` continuously until market close (3:30 PM)
- All positions persist to disk for recovery

**Log file**: `/Users/amitkumar/Personal/work/source code/SniperAgent/SmartAgent/logs/sniper_YYYYMMDD.log`

### 2. Post-Market Assessment — 3:30 PM Weekdays
**Schedule**: 3:30 PM Mon–Fri (cron: `30 15 * * 1-5`)  
**Task ID**: `sniper-agent-assessment`  
**Location**: `/Users/amitkumar/Documents/Claude/Scheduled/sniper-agent-assessment/SKILL.md`

**What it does:**
- Reviews all closed trades from the session
- Prints per-horizon weight evolution (what the learners discovered)
- Reports gate telemetry (why trades entered/were rejected)
- Generates daily performance summary
- Confirms swing/positional positions ready for next session
- Logs appended to same file as market hours

---

## Upstox Access Token Setup

The agent reads your token from the `UPSTOX_ACCESS_TOKEN` environment variable.

### Manual Daily Token Update (Required)

Since Upstox tokens expire daily, you must refresh yours each trading day:

```bash
# Get your fresh token from Upstox console, then set it:
export UPSTOX_ACCESS_TOKEN='your_fresh_token_here'

# Verify it's set:
echo $UPSTOX_ACCESS_TOKEN

# Optional: make it persistent across terminal sessions by adding to ~/.bash_profile or ~/.zshrc:
echo "export UPSTOX_ACCESS_TOKEN='your_token'" >> ~/.zshrc
source ~/.zshrc
```

**Timing**: Set the token **before 9:15 AM** on trading days.

---

## Horizon Configuration

### Disabled
- **intraday** (1-minute bars, 45-min max hold)
  - Would square off at 3:15 PM anyway
  - Removed per user request

### Enabled
- **short_term** (30-minute bars, 7-day max hold)
  - Capital sleeve: 25%
  - Learning rate: η = 0.08
  - Min profit target: 2% of entry

- **swing** (daily bars, 30-day max hold)
  - Capital sleeve: 20%
  - Learning rate: η = 0.06
  - Min profit target: 3% of entry

- **positional** (weekly bars, 120-day max hold)
  - Capital sleeve: 15%
  - Learning rate: η = 0.05
  - Min profit target: 5% of entry

**Cross-horizon coupling** (κ = 0.30): When independent horizons agree on a signal, each gets a +0.30 bounded boost to its conviction, but no horizon can be overtaken by other horizons' evidence.

---

## Wrapper Script Details

**File**: `/Users/amitkumar/Personal/work/source code/SniperAgent/SmartAgent/sniper_agent_wrapper.py`

The wrapper handles:

1. **Holiday Calendar** — NSE 2026 holidays hardcoded
   - To update for 2027+, edit `NSE_HOLIDAYS_2026` dict in wrapper
   - Dates: Republic Day, Independence Day, Diwali, Good Friday, etc.

2. **Weekday Validation** — Skips Saturdays/Sundays automatically

3. **Sleep Prevention** — Calls `caffeinate -i -t <seconds>`
   - Prevents idle sleep for the duration of market hours
   - If `caffeinate` fails, logs warning but continues (graceful fallback)

4. **Upstox Token Validation** — Checks `UPSTOX_ACCESS_TOKEN` is set and not empty

5. **Horizon Configuration** — Passes `HERMES_HORIZON_CONFIG` env var with disabled intraday

6. **Mode Selection**:
   - `--mode market-hours`: Continuous trading (default, 9:15 AM task)
   - `--mode assessment`: Post-market review (3:30 PM task)
   - `--force`: Skip holiday/weekday checks (testing only)

7. **Logging** — Writes to `logs/sniper_YYYYMMDD.log` with both file and console output

---

## Manual Testing

Before the first automatic run, you can test the setup:

```bash
# Test market hours mode (with token set):
python3 /Users/amitkumar/Personal/work/source\ code/SniperAgent/SmartAgent/sniper_agent_wrapper.py --mode market-hours

# Test assessment mode:
python3 /Users/amitkumar/Personal/work/source\ code/SniperAgent/SmartAgent/sniper_agent_wrapper.py --mode assessment

# Test without holiday/weekday validation (e.g., on weekend):
python3 /Users/amitkumar/Personal/work/source\ code/SniperAgent/SmartAgent/sniper_agent_wrapper.py --mode market-hours --force
```

---

## What the Agent Learns

The Hermes Omnihorizon engine is continuously adapting:

### Per-Horizon Learning (Hedge Update)
After each closed trade, weights update via:
```
w_h,i ← clip( w_h,i · exp(η_h · R · φ_i_entry), 0.10, 2.50 )
```
Where:
- **R** = realized R-multiple (clipped to [−2, 3])
- **φ_i** = evidence feature at entry (tanh-squashed)
- **η_h** = learning rate (intraday 0.10, positional 0.05)

### Adaptive Entry Threshold (p*)
- **After a loss**: p* += 0.015 (raise bar to avoid repeating mistake)
- **After a win**: p* -= 0.005 (lower bar, evidence working)
- **Bounds**: [0.55, 0.80]

### Calibration (Platt Scaling)
Raw posterior `p` is calibrated via SGD on closed-trade log-loss:
```
p_cal = σ(a·L + b)    where a, b adapt per horizon
```
Overconfidence shrinks `a` and pushes `b` negative, deflating predicted win rates toward realized ones.

### Diagnostics in Assessment Output
- **Earned Credit**: Actual learner verdict before mass-conservation dilution
- **Gate Stats**: Counts per gate (p*, IV cap, cooldown, cost, Kelly, min-bars, already-open)
- **Weight Evolution**: Per-horizon w_i progress showing which evidence matters

---

## Operational Checklist

**Daily (Before 9:15 AM)**
- [ ] Fetch fresh Upstox token from console
- [ ] Set `UPSTOX_ACCESS_TOKEN` in environment
- [ ] Verify token: `echo $UPSTOX_ACCESS_TOKEN`

**During Market Hours (9:15 AM – 3:30 PM)**
- [ ] Agent runs automatically
- [ ] MacBook stays awake (no sleep prompts)
- [ ] Check logs if unexpected behavior: `tail -f logs/sniper_YYYYMMDD.log`

**After Market Close (3:30 PM+)**
- [ ] Assessment runs automatically
- [ ] Review daily report in logs
- [ ] Note any trades, weight changes, or anomalies
- [ ] Check overnight positions ready for next day

**Weekly / Monthly**
- [ ] Review weight evolution trends (learning working?)
- [ ] Check calibration drift (a, b parameters stable?)
- [ ] Verify no position zombies (stuck trades)
- [ ] Monitor gate rejection stats (why aren't we trading?)

---

## Troubleshooting

### **Task doesn't run at scheduled time**
- Verify Cowork app is open and connected
- Check scheduled task list in Cowork sidebar
- Manually trigger "Run now" to test task execution

### **"UPSTOX_ACCESS_TOKEN not set" error**
```bash
# Make sure you set it before 9:15 AM:
export UPSTOX_ACCESS_TOKEN='your_token'

# Verify:
echo $UPSTOX_ACCESS_TOKEN
```

### **MacBook sleeps during market hours**
- Check if `caffeinate` command exists (macOS only)
- If wrapper can't find it, it logs a warning but continues
- Manual fallback: Run `caffeinate -i` in a separate terminal window

### **No trades placed even when signal strong**
- Check logs for gate rejections (cost, IV cap, cooldown, Kelly zero, min-bars)
- Verify at least 30 bars of history loaded for horizon
- Confirm Upstox data fetch not failing
- Check if horizon is disabled in config

### **Assessment shows no closed trades**
- If first day, learners may have high p* thresholds (0.70 default)
- Multiple positions may still be open (held > 1 day)
- Verify data fetch succeeded (no outage logs)

### **Swap intraday back on (revert config)**
Edit `sniper_agent_wrapper.py`, change:
```python
success = run_allstrategy(mode="market-hours", disable_intraday=False)
```
Then restart market hours session.

---

## Key Parameters Reference

| Parameter | Value | Meaning |
|-----------|-------|---------|
| `CAPITAL` | ₹10,00,000 | Nominal paper trading capital |
| `KAPPA` | 0.30 | Cross-horizon coupling bound |
| `BIAS0` | −1.0 | Prior log-odds (scepticism) |
| `p_base` | 0.70 | Entry threshold (adaptive, bounds 0.55–0.80) |
| `min_profit_pct` | — / 2% / 3% / 5% | Min ATR-target per horizon |
| `KELLY_FRACTION` | 0.25 | Quarter-Kelly sizing |
| `RISK_CAP` | 0.05 | Max 5% sleeve risk per trade |
| `EWMA_LAMBDA` | 0.94 | RiskMetrics vol decay |
| `PCR_MODE` | "delta" | Windowed ΔPCR (not legacy level) |

---

## File Structure

```
SmartAgent/
├── allstrategy.py                 # Main engine (read-only during runs)
├── allstrategy.md                 # Strategy documentation
├── sniper_agent_wrapper.py        # Orchestration wrapper (new)
├── SNIPER_AGENT_SETUP.md          # This file
├── logs/
│   └── sniper_20260612.log        # Daily logs
└── .hermes_brain_state.json       # Learner state (auto-created)
```

---

## Next Steps

1. **Set Upstox token** (before 9:15 AM tomorrow):
   ```bash
   export UPSTOX_ACCESS_TOKEN='your_token'
   ```

2. **Run manual test** (optional, verify setup works):
   ```bash
   python3 sniper_agent_wrapper.py --mode market-hours --force
   ```

3. **Scheduled tasks are live** — Agent will start automatically at 9:15 AM on next trading day

4. **Monitor logs** the first week to ensure smooth operation and learning progress

---

## Support / Questions

- Strategy details: See `allstrategy.md`
- Wrapper code: See `sniper_agent_wrapper.py` (well-commented)
- Daily assessment output: Check `logs/sniper_YYYYMMDD.log` after 3:30 PM
- Upstox API docs: https://api.upstox.com/

Good luck with your autonomous trading agent! 🎯
