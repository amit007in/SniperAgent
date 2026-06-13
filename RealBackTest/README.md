# RealBackTest — training the Sniper Agent on real NSE data

Battle-tests `SmartAgent/allstrategy.py` (**unmodified**) on real Upstox
history — real candlesticks at every horizon and a real options plane —
then judges it against luck, costs and statistics before any live promotion.

## The hard problem this solves

Upstox historical option data (expired-instruments API) provides **OHLCV +
open interest only** — no IV, no greeks, no bid/ask depth. The engine's
evidence vector needs all three. Reconstruction strategy:

| engine input | live source | historical reconstruction | nature |
|---|---|---|---|
| `pcr` (x7) | chain CE/PE OI | **real** per-minute OI from expired-contract candles | real data |
| `iv` → `vrp` (x8), IV-cap gate | chain greeks | **Black-Scholes inversion** of the real ATM premium vs real spot, strike, time-to-expiry (Brent root-finding; put-call-parity fallback; time-aware EWMA + jump filter) | real, computed |
| `flow` (x6) | bid/ask qty | **Bulk Volume Classification** (Easley, López de Prado, O'Hara 2012) on the ATM CE — volume split into buy/sell by the CDF of standardised price change | principled proxy |

The flow proxy is the only input without a direct historical counterpart, so
every run includes a **flow-ablation pass** (x6 = 0). If the edge dies when
the proxy is removed, the report says so — the proxy is never allowed to
manufacture the verdict.

When option data is missing/stale at a minute (or the expired-instruments
API needs the Upstox Plus plan — error UDAPI1149), the engine degrades to
structure-only with the `P_STAR_NO_OPTIONS` floor: the **identical** live
failure path.

## No lookahead, by construction

- 30-min bars become visible only after they close; daily bars the next
  day; weekly bars the next week — the exact information set live had.
- Options joined backward as-of with a 15-min staleness cap.
- The clock, `datetime.now()`, `fetch_bars` and `fetch_atm_chain_row` are
  patched; fusion, Platt calibration, gates, Kelly, bar-walk exits,
  re-underwriting and learning run untouched.
- `selftest` *proves* the property: chain rows and bar slices are
  byte-identical when all future data is deleted.

## Quick start (your machine)

```bash
cd RealBackTest

# 0) trust the pipeline first — BS math, known-IV recovery, lookahead, e2e
python3 realbacktest.py selftest

# 1) fresh Upstox token (expires daily)
export UPSTOX_ACCESS_TOKEN='...'

# 2) fill the cache (resumable; re-run after any interruption)
python3 realbacktest.py fetch --start 2024-06-01 --end 2026-06-10

# 3) sanity-check coverage
python3 realbacktest.py audit

# 4) backtest + ablation + benchmarks + report
python3 realbacktest.py run --start 2024-06-01 --end 2026-06-10
```

Output: `reports/realworthiness_report_main.md` (verdict + all evidence),
`reports/runs/main/` (trades.csv, summary.json).

Useful flags for `run`: `--horizons intraday,short_term` (subset),
`--symbols RELIANCE,ICICIBANK`, `--cost-bps 2`, `--tag <name>`,
`--no-ablation`, `--engine-log` (keep full engine stdout).

## What the fetch costs

2 years, 5 symbols, options for 3: roughly 120 one-month 1-min equity
chunks + ~50 coarser chunks + ~25 expiries × 3 symbols of contracts +
option candles for ATM±2 strikes per expiry — order of **3–6k requests**,
throttled at 3 req/s ⇒ **~30–60 minutes**, all cached in SQLite
(`data/marketdata.db`) and never re-fetched. Strike pre-selection keeps it
small: only strikes that were ATM (±2 neighbours) on some session are
pulled, not whole chains.

Runtime of `run` on 2 years × 5 symbols × 4 horizons: the intraday horizon
evaluates every minute, so expect a few hours for main + ablation passes.
Subset with `--horizons`/`--symbols` for fast iterations.

## How to read the verdict

`GO` requires, simultaneously: positive mean net R with block-bootstrap
p < 0.05; the strategy's mean R above the 95th percentile of random-entry
portfolios using the *same* exits and costs; IV recovery ≥ 90% of
option-active bars; max drawdown ≤ 10% of capital; deflated Sharpe > 0.5;
learning not degrading between halves; edge surviving flow ablation; and
≥ 30 closed trades. Anything less is NO-GO or INCONCLUSIVE — by design.

Note: `selftest` ends in a NO-GO verdict on its fixture. That is correct
behaviour — the fixture plants a 1-minute AR(1) edge that round-trip costs
consume (the cost-floor trap the synthetic battery documented), and an
honest assessment must fail it. The self-test asserts the *pipeline*, not
a green verdict.

## Layout

```
RealBackTest/
├── realbacktest.py        # CLI: selftest | fetch | audit | run
├── rbt/
│   ├── config.py          # universe, window, thresholds, paths
│   ├── upstox_data.py     # V3 + expired-instruments fetchers, SQLite cache
│   ├── iv_engine.py       # BS pricing/inversion, greeks, IVTracker, BVC
│   ├── chain_replay.py    # per-minute ATM chain-row reconstruction
│   ├── harness.py         # engine patching + multi-symbol replay loop
│   ├── benchmarks.py      # random-entry null, bootstrap, DSR, buy & hold
│   ├── report.py          # assessment + GO/NO-GO report writer
│   ├── fixtures.py        # known-IV synthetic dataset for selftest
│   └── selftest.py        # 4-stage offline verification
├── tests/test_pipeline.py # pytest wrappers around selftest stages
├── data/                  # marketdata.db cache (auto temp-dir fallback)
├── db/                    # throwaway per-run engine DBs
└── reports/               # realworthiness_report_*.md + runs/<tag>/
```

If `data/`/`db/` sit on a cloud-synced folder where SQLite locking fails,
the code falls back to the system temp dir automatically (override with
`RBT_DATA_DIR` / `RBT_DB_DIR`).

## Notes & limits

- IV inversion assumes European exercise (fine for calls on non-dividend
  stocks, near-exact ATM short-dated), `r = 6.5%`. ATM is the
  best-conditioned strike for inversion — and the only row the engine uses.
- Stock options are monthly expiries; PCR-delta dynamics across the roll
  are handled by keying the IV/flow state per expiry.
- 1-min history exists from Jan 2022 → don't set `--start` earlier.
- This is research tooling, not financial advice. A GO verdict here means
  "promote to forward paper-trading", not "wire real money".
