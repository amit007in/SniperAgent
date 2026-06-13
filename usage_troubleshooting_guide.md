# SniperAgent — Usage & Troubleshooting Guide

> Last updated: June 2026  
> Workspace root: `/Users/amitkumar/Personal/work/source code/SniperAgent/`

---

## Directory Layout

```
SniperAgent/
├── Data/                          ← all runtime artifacts (DBs, logs, reports)
│   ├── marketdata.db              ← shared NSE market data (equity + options candles)
│   ├── backups/                   ← safe DB backups (created by backup_marketdata.sh)
│   ├── SmartAgent/
│   │   ├── hermes_omnihorizon_v2.db   ← live agent brain state
│   │   ├── logs/                  ← sniper_YYYYMMDD.log
│   │   └── reports/               ← sniper_report_YYYYMMDD.txt
│   ├── RealBackTest/
│   │   ├── fixture.db             ← selftest fixture (auto-regenerated)
│   │   ├── db/                    ← rbt_<tag>.db per backtest run
│   │   └── reports/               ← realworthiness_report_<tag>.md + runs/
│   ├── PriceActionAgent/
│   │   ├── price_action.db        ← Claude narrative store
│   │   └── logs/
│   └── DailyFetch/
│       └── logs/                  ← fetch_YYYY-MM-DD.log
│
├── DailyFetch/                    ← daily fetch scripts (code only)
│   ├── daily_fetch.py
│   ├── backup_marketdata.sh
│   └── README.md
├── SmartAgent/                    ← live trading engine (code only)
├── RealBackTest/                  ← backtest harness (code only)
└── PriceActionAgent/              ← price action narrative agent (code only)
```

---

## Upstox Token — Daily Requirement

Upstox access tokens **expire at midnight every day**. You must export a fresh
token before running any command that calls the Upstox API.

### Get a fresh token
Log in to [Upstox Developer Console](https://developer.upstox.com) → API Keys →
generate a new access token each morning.

### Set it (two options — pick one)

**Option A — environment variable (required for automated scheduled fetch):**
```bash
export UPSTOX_ACCESS_TOKEN='<your_fresh_token>'
```

**Option B — token file (convenient fallback for manual runs):**
```bash
echo '<your_fresh_token>' > ~/.sniper_token
```

> The DailyFetch script and SmartAgent both check env var first, then
> `~/.sniper_token`. Set the env var before 4 pm IST each trading day so the
> scheduled fetch picks it up.

---

## Common Daily Workflow

### 1. Morning — start live agent
```bash
cd "/Users/amitkumar/Personal/work/source code/SniperAgent/SmartAgent"
export UPSTOX_ACCESS_TOKEN='<token>'
python3 allstrategy.py
```

### 2. After market close (4 pm IST) — daily fetch runs automatically
The Cowork scheduled task `sniper-daily-fetch` fires at 4:10 pm IST Mon–Fri.
Check the result in `Data/DailyFetch/logs/fetch_<today>.log`.

### 3. Manual fetch (if scheduled task missed or token was stale)
```bash
cd "/Users/amitkumar/Personal/work/source code/SniperAgent/RealBackTest"
export UPSTOX_ACCESS_TOKEN='<token>'
python3 realbacktest.py fetch --equity-only
```

### 4. Run a backtest (quick / ad-hoc)
```bash
cd "/Users/amitkumar/Personal/work/source code/SniperAgent/RealBackTest"
python3 realbacktest.py selftest                        # verify pipeline first
python3 realbacktest.py audit                           # check data coverage
python3 realbacktest.py run --tag <name>                # full backtest + report
```

### 5. Run the NSE-100 full 2-year backtest (first time or after fresh fetch)
```bash
cd "/Users/amitkumar/Personal/work/source code/SniperAgent/RealBackTest"
python3 realbacktest.py run --tag nse100_full
```
- Covers the default window: `2024-06-01 → 2026-06-10`
- **No token needed** — reads entirely from local `marketdata.db`; token expiry cannot interrupt it
- Runs both main pass and flow-ablation pass — required for a complete GO/NO-GO verdict
- Q1–Q3 2024 is cold-start burn-in (brain calibrates, ignore these results)
- Q4-2024 onwards is where the learned edge accumulates — judge the run here
- Expected duration: **16–32 hours** for 100 symbols × 2 years (both passes) — leave over a weekend
- Brain state DB preserved at `Data/RealBackTest/db/rbt_nse100_full.db`
- Report written to `Data/RealBackTest/reports/realworthiness_report_nse100_full.md`

### 6. Seed live brain from approved backtest (one-time, before going live)

The brain state at the end of the full 2-year run reflects learning across
all 100 symbols with Q1–Q3 2024 as burn-in and Q4-2024 → Q1-2026 as
productive learning. Seed this into the live agent so it does not start cold.

**What is seeded:** weights, p*, Platt calibration (a, b), trade count, wins — per horizon
**What is NOT seeded:** trades log, open positions (backtest artifacts, not real)

```bash
cd "/Users/amitkumar/Personal/work/source code/SniperAgent/DailyFetch"

# Step 1 — inspect before writing (mandatory review)
python3 seed_live_brain.py --dry-run
# Prints per-horizon: p*, hit rate, calibration (a, b), top 5 features
# Only proceed if the numbers look sound

# Step 2 — seed if approved
python3 seed_live_brain.py
# Writes to Data/SmartAgent/hermes_omnihorizon_v2.db
# Live agent loads these weights on next startup

# To seed from a different approved run tag:
python3 seed_live_brain.py --src "../Data/RealBackTest/db/rbt_<tag>.db"
```

> After seeding, start `allstrategy.py` normally — it loads the seeded weights
> automatically and continues learning from there. No cold start.

### 7. Safe DB backup (before any file operations)
```bash
bash "/Users/amitkumar/Personal/work/source code/SniperAgent/DailyFetch/backup_marketdata.sh"
# Saved to Data/backups/marketdata_YYYYMMDD_HHMMSS.db
```

---

## RealBackTest Commands

| Command | Token needed? | Resumable? | Duration |
|---|---|---|---|
| `python3 realbacktest.py selftest` | No | N/A | ~30 sec |
| `python3 realbacktest.py audit` | No | N/A | ~5 sec |
| `python3 realbacktest.py fetch --equity-only` | **Yes** | ✅ Yes | 1–1.5 hrs (first run); < 2 min (incremental) |
| `python3 realbacktest.py fetch` | **Yes** | ✅ Yes | 8–12 hrs (first run); incremental after |
| `python3 realbacktest.py run --tag <name>` | No | ❌ No | 1–16 hrs depending on universe |
| `python3 realbacktest.py run --tag <name> --no-ablation` | No | ❌ No | Faster — skips flow-ablation pass |
| `python3 realbacktest.py run --tag <name> --legacy` | No | ❌ No | A/B baseline: Hermes V2 disabled |

**Fetch is resumable; run is not.** If the token expires during a fetch, export
a fresh token and re-run the same command — it skips completed chunks. If a run
crashes mid-way, restart from scratch (runs read only from local DB, so token
expiry cannot interrupt them).

### Staged fetch (recommended order for first time)
```bash
# Stage 1: equity bars — fast, gets you a runnable backtest today
python3 realbacktest.py fetch --equity-only

# Stage 2: options plane — adds IV/PCR/flow reconstruction
# Run overnight or over a weekend; token will expire mid-fetch — just
# export a fresh token next morning and re-run the same command
python3 realbacktest.py fetch
```

---

## Environment Variable Overrides

| Variable | Default | Purpose |
|---|---|---|
| `UPSTOX_ACCESS_TOKEN` | — | Upstox API token (required for fetch + live) |
| `OMNIHORIZON_DB` | `Data/SmartAgent/hermes_omnihorizon_v2.db` | Override brain state DB path |
| `RBT_DATA_DIR` | `Data/` | Override marketdata.db location |
| `RBT_DB_DIR` | `Data/RealBackTest/db/` | Override run engine DB location |
| `RBT_REPORT_DIR` | `Data/RealBackTest/reports/` | Override report output location |
| `RBT_FIXTURE_DB` | `Data/RealBackTest/fixture.db` | Override selftest fixture DB |
| `MARKET_DATA_DB` | `Data/marketdata.db` | Override for PriceActionAgent |
| `PA_DB` | `Data/PriceActionAgent/price_action.db` | Override PA narrative DB |
| `PA_LOG_DIR` | `Data/PriceActionAgent/logs/` | Override PA log directory |

---

## Troubleshooting

### ❌ `sqlite3.DatabaseError: database disk image is malformed`

**Cause:** DB was copied with `cp` while a WAL journal was open, or process
was force-killed mid-write on a non-WAL DB.

**Fix:**
```bash
cd "/Users/amitkumar/Personal/work/source code/SniperAgent/Data"

# 1. Try dump rescue
sqlite3 marketdata.db ".dump" > marketdata_dump.sql 2>/dev/null
wc -l marketdata_dump.sql   # if millions of lines, data is recoverable

# 2. Restore from dump
mv marketdata.db marketdata.db.bak
sqlite3 marketdata.db < marketdata_dump.sql
sqlite3 marketdata.db "SELECT COUNT(*) FROM equity_candles;"

# 3. If dump is tiny (< 100 lines) — unrecoverable, re-fetch
rm marketdata.db marketdata.db.bak marketdata_dump.sql
cd ../RealBackTest && python3 realbacktest.py fetch --equity-only
```

**Prevention:** Always use `backup_marketdata.sh` before file operations.
Never use `cp` on a live SQLite file.

---

### ❌ `401 Unauthorized`

**Cause:** Upstox access token has expired (tokens expire at midnight).

**Fix:**
```bash
# Get a fresh token from Upstox Developer Console, then:
export UPSTOX_ACCESS_TOKEN='<new_token>'
# Re-run your command — fetch is resumable, no data is lost
```

---

### ❌ `429 Too Many Requests` during fetch

**Cause:** Upstox 30-min quota (2000 req/30 min) briefly exceeded.

**Fix:** Nothing — the rate limiter handles this automatically. It will wait
up to 12 patient retries (45 s each), then resume. Do not interrupt the fetch.
If it persists beyond 10 minutes, wait 30 minutes and re-run.

---

### ❌ `No Upstox access token found` in DailyFetch log

**Cause:** Scheduled task fired but `UPSTOX_ACCESS_TOKEN` was not set and
`~/.sniper_token` was empty or stale.

**Fix:** Set the token before 4 pm IST each trading day:
```bash
export UPSTOX_ACCESS_TOKEN='<token>'
# OR
echo '<token>' > ~/.sniper_token
```
Then manually trigger the missed fetch:
```bash
cd "/Users/amitkumar/Personal/work/source code/SniperAgent/RealBackTest"
python3 realbacktest.py fetch --equity-only
```

---

### ❌ `UDAPI1149` error during options fetch

**Cause:** Your Upstox plan does not include the expired-instruments
historical candle endpoint (options history requires Upstox Plus or above).

**Fix:** Upgrade to Upstox Plus, or run equity-only fetches and use
structure-only mode for backtest (options features will be skipped gracefully).
The live agent is unaffected — it reads the live options chain directly.

---

### ❌ `IV recovery < 90%` in backtest report

**Cause:** The strike grid in the DB is too narrow — the spot price wandered
past the cached strikes during intraday moves, leaving gaps.

**Fix:** `STRIKE_NEIGHBOURS` is already set to 4 in `RealBackTest/rbt/config.py`.
If still low for a specific symbol, re-fetch that symbol with a wider net:
```bash
python3 realbacktest.py fetch --symbols HDFCBANK
```

---

### ❌ Backtest runs but 0 trades / all gates fire

**Cause:** Usually the warm-up period — the brain needs 25+ sessions before
p* calibration kicks in. Check the gate telemetry in the report under
`reports/runs/<tag>/summary.json`.

**Fix:** Ensure `--start` is at least 2 months before the period you want to
evaluate, or check the `min_bars` gate is not blocking all evaluations.

---

### ❌ `SQLite on cloud-synced folder` fallback message

**Cause:** `Data/` is inside an iCloud/Dropbox synced folder which blocks
SQLite locking. The code automatically falls back to a system temp directory.

**Fix (permanent):** Move `Data/` outside the synced folder:
```bash
export RBT_DATA_DIR=/Users/amitkumar/sniper_data
export OMNIHORIZON_DB=/Users/amitkumar/sniper_data/hermes_omnihorizon_v2.db
```
Add these exports to your `~/.zshrc` or `~/.bash_profile`.

---

## Safe DB Copy Rule

| Operation | Safe method |
|---|---|
| Backup marketdata.db | `bash DailyFetch/backup_marketdata.sh` |
| Copy to external drive | `sqlite3 marketdata.db ".backup '/path/to/copy.db'"` |
| ~~Regular file copy~~ | ~~`cp marketdata.db ...`~~ — **never do this** |

The `.backup` command uses SQLite's online backup API — safe even while a
fetch is actively writing to the DB.

---

## Log Locations Quick Reference

| Component | Log path |
|---|---|
| Live agent (SmartAgent) | `Data/SmartAgent/logs/sniper_YYYYMMDD.log` |
| Daily fetch | `Data/DailyFetch/logs/fetch_YYYY-MM-DD.log` |
| PriceActionAgent | `Data/PriceActionAgent/logs/daily_update.log` |
| Backtest run (engine) | `Data/RealBackTest/reports/runs/<tag>/` |
| Analyzer reports | `Data/SmartAgent/reports/sniper_report_YYYYMMDD.txt` |
