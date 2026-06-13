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

### 4. Run a backtest
```bash
cd "/Users/amitkumar/Personal/work/source code/SniperAgent/RealBackTest"
python3 realbacktest.py selftest                        # verify pipeline first
python3 realbacktest.py audit                           # check data coverage
python3 realbacktest.py run --tag v2_trend              # full backtest + report
```

### 5. Safe DB backup (before any file operations)
```bash
bash "/Users/amitkumar/Personal/work/source code/SniperAgent/DailyFetch/backup_marketdata.sh"
# Saved to Data/backups/marketdata_YYYYMMDD_HHMMSS.db
```

---

## RealBackTest Commands

| Command | What it does | Duration |
|---|---|---|
| `python3 realbacktest.py selftest` | Offline pipeline verification (no token needed) | ~30 sec |
| `python3 realbacktest.py audit` | Coverage report for all symbols in DB | ~5 sec |
| `python3 realbacktest.py fetch --equity-only` | Equity bars only for all 100 symbols | 1–1.5 hrs (first run); < 2 min (incremental) |
| `python3 realbacktest.py fetch` | Equity + options plane (49 symbols) | 8–12 hrs (first run); incremental after |
| `python3 realbacktest.py run --tag <name>` | Full backtest + flow ablation + report | 1–8 hrs depending on universe |
| `python3 realbacktest.py run --tag <name> --legacy` | Same but with Hermes V2 disabled (A/B baseline) | same |

All commands are **resumable** — if interrupted, re-run the same command and it
picks up from the last completed chunk.

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
