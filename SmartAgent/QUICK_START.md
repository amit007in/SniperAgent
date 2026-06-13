# Sniper Agent — Quick Start

## TL;DR Setup (5 minutes)

### Before Your First Trading Day

```bash
# 1. Set your Upstox token (fresh daily):
export UPSTOX_ACCESS_TOKEN='your_upstox_token_here'

# 2. Verify it's set:
echo $UPSTOX_ACCESS_TOKEN

# 3. (Optional) Test the wrapper on a non-trading day:
cd /Users/amitkumar/Personal/work/source\ code/SniperAgent/SmartAgent
python3 sniper_agent_wrapper.py --mode market-hours --force

# That's it! Scheduled tasks are already set up.
```

---

## Automated Schedule

| Time | Day | What | Task ID |
|------|-----|------|---------|
| **9:15 AM** | Mon–Fri | Start trading (short_term, swing, positional; NO intraday) | `sniper-agent-market-hours` |
| **3:30 PM** | Mon–Fri | Post-market assessment & report | `sniper-agent-assessment` |

Automatically **skips** weekends and NSE holidays.

---

## Daily Routine

**Morning (before 9:15 AM)**
```bash
export UPSTOX_ACCESS_TOKEN='your_fresh_token'
# Agent starts automatically at 9:15 AM
```

**Evening (after 3:30 PM)**
```bash
# Check today's log:
tail -50 logs/sniper_$(date +%Y%m%d).log

# Look for:
# - Closed trades (PROFIT/LOSS/TRAILED)
# - Weight evolution (learning signals)
# - Open positions (short_term, swing, positional)
```

---

## Key Features

✅ Market hours only: 9:15 AM – 3:30 PM IST  
✅ Weekdays + NSE holidays excluded  
✅ MacBook stays awake (no sleep interrupts)  
✅ Intraday disabled, short_term/swing/positional active  
✅ Upstox token from environment (manual daily update)  
✅ Crash-safe: positions persist to disk  
✅ Self-learning: weights adapt per horizon  
✅ Post-market assessment (3:30 PM)

---

## File Locations

```
/Users/amitkumar/Personal/work/source code/SniperAgent/SmartAgent/

sniper_agent_wrapper.py      ← Orchestration (runs at 9:15 AM & 3:30 PM)
allstrategy.py               ← Engine (called by wrapper)
SNIPER_AGENT_SETUP.md        ← Full documentation
QUICK_START.md               ← This file
logs/sniper_YYYYMMDD.log     ← Daily trading log
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "UPSTOX_ACCESS_TOKEN not set" | `export UPSTOX_ACCESS_TOKEN='your_token'` |
| Task doesn't run at 9:15 AM | Verify Cowork app is open; check Scheduled tasks in sidebar |
| MacBook sleeps anyway | `caffeinate -i` will be called; if macOS, should prevent sleep |
| No trades despite signal | Check logs: `tail logs/sniper_YYYYMMDD.log` for gate rejections |
| Want to enable intraday again | Edit `sniper_agent_wrapper.py` line where it calls `run_allstrategy(disable_intraday=False)` |

---

## Horizon Details

| Horizon | Bars | Max Hold | Capital | Enabled? |
|---------|------|----------|---------|----------|
| intraday | 1-min | 45 min | 40% | ❌ NO |
| short_term | 30-min | 7 days | 25% | ✅ YES |
| swing | daily | 30 days | 20% | ✅ YES |
| positional | weekly | 120 days | 15% | ✅ YES |

---

## What It Learns

After each trade closes, the agent adapts:
- **Weights**: Which evidence features work? (structure, volume, momentum, PCR, variance ratio, flow, etc.)
- **Entry Threshold (p*)**: How strict should entry be? (rises after losses, falls after wins)
- **Calibration**: Is the predicted win rate honest vs. realized?

Each horizon learns independently — short_term won't contaminate swing learner.

---

## Monitoring

```bash
# Real-time log (live during market hours):
tail -f logs/sniper_$(date +%Y%m%d).log

# Count trades today:
grep "PROFIT\|LOSS\|TRAILED" logs/sniper_$(date +%Y%m%d).log | wc -l

# See open positions:
grep "OPEN\|re-hydrated" logs/sniper_$(date +%Y%m%d).log
```

---

## Full Documentation

See `SNIPER_AGENT_SETUP.md` for:
- Detailed parameter reference
- Holiday calendar (update for future years)
- Learning mechanics (weight updates, calibration)
- Manual testing procedures
- Advanced troubleshooting

---

**Ready? Set your token and let the agent run!** 🚀
