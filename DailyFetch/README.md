# DailyFetch — Post-Market Incremental Data Update

Keeps `Data/marketdata.db` current by pulling today's equity candles from
Upstox after NSE market close. Runs automatically at **4:10 pm IST, Mon–Fri**
via the Cowork scheduled task `sniper-daily-fetch`.

## What it fetches

- Equity bars (1-min, 30-min, daily, weekly) for all 100 NSE symbols
- Only the **new candles since the last fetch** — fully incremental, typically
  completes in under 2 minutes for 100 symbols
- Options plane is **not** re-fetched here — historical expired-contract data
  is a one-time backfill done via `realbacktest.py fetch`; the live agent reads
  the live options chain from the Upstox API directly at runtime

## Output

| Path | Description |
|---|---|
| `Data/marketdata.db` | Updated in-place with today's candles |
| `Data/DailyFetch/logs/fetch_YYYY-MM-DD.log` | Per-run structured log |

## Token setup (required daily)

Upstox tokens expire at midnight. You must provide a fresh token before 4 pm
each day using **one** of these methods:

**Method 1 — environment variable (recommended for automated runs):**
```bash
export UPSTOX_ACCESS_TOKEN='<your_token>'
```

**Method 2 — token file (convenient for manual runs):**
```bash
echo '<your_token>' > ~/.sniper_token
```
The script reads `~/.sniper_token` as a fallback if the env var is not set.

## Manual run

```bash
cd "/Users/amitkumar/Personal/work/source code/SniperAgent/DailyFetch"
export UPSTOX_ACCESS_TOKEN='<your_token>'
python3 daily_fetch.py
```

## Scheduled task

The Cowork task `sniper-daily-fetch` runs this script at **10:10 UTC
(4:10 pm IST)** Monday–Friday. It:
1. Reads the token from `UPSTOX_ACCESS_TOKEN` (set it before market close)
2. Checks if today is an NSE trading day — skips weekends and holidays
3. Runs `realbacktest.py fetch --equity-only`
4. Logs the result to `Data/DailyFetch/logs/fetch_<date>.log`

## NSE holiday calendar

The holiday list in `daily_fetch.py` covers 2025–2026. Update the
`NSE_HOLIDAYS` set at the start of each year to add new dates.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No Upstox access token found` | Export a fresh token or write to `~/.sniper_token` |
| `429 Too Many Requests` in rbt log | Rate limiter will retry automatically; if it persists, wait 30 min and re-run |
| `401 Unauthorized` | Token has expired — get a new one from Upstox |
| Fetch exits with non-zero code | Check `Data/DailyFetch/logs/fetch_<date>.log` for `[rbt]` lines |
