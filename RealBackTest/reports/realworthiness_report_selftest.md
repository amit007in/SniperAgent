# Real-Data Worthiness Report — `selftest`
_Generated 2026-06-12T20:45:54.599965+00:00Z · window **2026-04-06 → 2026-04-24** · costs **10.0 bps round-trip** · horizons intraday, short_term · symbols FIXTURE_

## VERDICT: **NO-GO — see failed checks**

| check | result | detail |
|---|---|---|
| Positive mean net R | FAIL | mean R = -0.459 over 17 closed trades |
| Bootstrap p < 0.05 | FAIL | p = 0.9345 |
| Beats random entries (median pctile ≥ 95.0) | FAIL | median pctile = 94.0, worst cell = 94.0 |
| IV recovery ≥ 90% | PASS | worst symbol = 100.0% |
| Max DD ≤ 10% of capital | FAIL | ₹235,807 vs ₹1,000,000 |
| Deflated Sharpe > 0.5 (prob true SR > 0) | FAIL | DSR = 0.003 |
| Learning not degrading (2nd half ≥ 1st half − 0.1R) | FAIL | +0.162 → -1.011 |
| Sample size ≥ 30 closed trades | FAIL | n = 17 |

## 1. Headline (closed trades, net of costs)
| metric | value |
|---|---|
| closed trades | 17 |
| hit rate | 41.2% |
| mean net R | -0.459 |
| per-trade Sharpe | -0.319 |
| deflated Sharpe (prob) | 0.003 |
| bootstrap p (mean R > 0) | 0.9345 |
| total net PnL | ₹-156,014 |
| max drawdown | ₹235,807 |
| outcomes | {"LOSS": 8, "PROFIT": 7, "TRAILED": 2} |
| open at end (excluded) | 0 |

## 2. Per-horizon
| horizon | trades | hit | mean R | t-stat | net PnL ₹ | p* end | calib a/b |
|---|---|---|---|---|---|---|---|
| intraday | 17 | 41.2% | -0.459 | -1.32 | -156,014 | 0.711 | 0.50/-0.41 |

## 3. Strategy vs random-entry null (same exits, same costs)
| cell | n | strategy mean R | null mean | null p95 | strategy pctile |
|---|---|---|---|---|---|
| intraday/FIXTURE | 17 | -0.459 | -0.891 | -0.415 | 94.0 |

_Read: pctile is where the strategy's mean R lands in 200 random-entry portfolios of the same size. > 95 means the evidence gating beats luck._

## 4. Buy-and-hold reference
| symbol | total return | ann vol | Sharpe | max DD |
|---|---|---|---|---|

_The agent risks a few % of one sleeve per trade — compare risk-adjusted, not absolute, returns._

## 5. Walk-forward progression (online learning)
| fold | window | trades | mean R | hit | net PnL ₹ |
|---|---|---|---|---|---|
| 1 | 2026-04-09→2026-04-13 | 9 | -0.061 | 56% | -11,027 |
| 2 | 2026-04-13→2026-04-17 | 1 | -1.964 | 0% | -39,281 |
| 3 | 2026-04-17→2026-04-20 | 1 | -0.418 | 0% | -8,357 |
| 4 | 2026-04-20→2026-04-24 | 6 | -0.811 | 33% | -97,349 |

First half mean R **+0.162** → second half **-1.011** (degrading).

## 6. Flow-proxy ablation (x6 = 0)

## 7. Options-plane reconstruction quality
| symbol | chain calls | served | availability | IV success | parity rescues | stale | no contract |
|---|---|---|---|---|---|---|---|
| FIXTURE | 4883 | 4883 | 100.0% | 100.0% | 0 | 0 | 0 |

_IV recovered by Black-Scholes inversion of real ATM premiums; PCR from real per-minute OI; flow via BVC. When availability gaps occur the engine degrades to structure-only with the P_STAR_NO_OPTIONS floor — the identical live failure path._

## 8. Gate telemetry (why it didn't trade)
| gate | intraday | short_term |
|---|---|---|
| align_veto | 87 | 0 |
| already_open | 115 | 0 |
| cooldown | 68 | 0 |
| cost_gate | 2 | 0 |
| entered | 17 | 0 |
| evaluated | 4850 | 195 |
| min_bars | 435 | 0 |
| p_star | 4676 | 195 |
| shadow_loss | 20 | 1 |
| shadow_open | 39 | 2 |
| shadow_win | 19 | 1 |

## 9. Learning evolution (Δ weight, earned credit)

**intraday**
| feature | Δw | credit |
|---|---|---|
| structure | +0.156 | +0.787 |
| value | +0.095 | +0.773 |
| vol_anom | -0.015 | +0.332 |
| mom_t | +0.038 | +0.577 |
| vratio | -0.049 | +0.056 |
| flow | +0.053 | +0.534 |
| pcr | -0.045 | +0.208 |
| vrp | -0.050 | +0.112 |
| maturity | -0.052 | +0.101 |
| accel | -0.058 | +0.066 |
| align | -0.098 | -0.033 |
| mom_l | +0.065 | +0.627 |
| mom_v | -0.042 | +0.162 |

**short_term**
| feature | Δw | credit |
|---|---|---|
| structure | -0.003 | -0.011 |
| value | +0.001 | +0.001 |
| vol_anom | -0.000 | -0.004 |
| mom_t | -0.002 | -0.009 |
| vratio | -0.000 | -0.003 |
| flow | +0.004 | +0.014 |
| pcr | -0.002 | -0.009 |
| vrp | -0.001 | -0.007 |
| maturity | +0.000 | -0.002 |
| accel | -0.001 | -0.010 |
| align | +0.003 | +0.007 |
| mom_l | +0.000 | -0.003 |
| mom_v | +0.000 | -0.000 |

## 10. Data audit
```json
{
  "fixture": true
}
```

## Method notes
- Engine under test: `SmartAgent/allstrategy.py`, unmodified — clock, bar feed and chain feed patched; fusion, calibration, gates, Kelly, exits and learning untouched.
- No lookahead: 30-min bars visible only after close, daily bars next day, weekly bars next week; options joined backward as-of with a 15-min staleness cap.
- IV by Brent inversion of Black-Scholes on real premiums (r = 6.5%); put-call-parity fallback; EWMA smoothing with jump filter.
- Flow x6 via Bulk Volume Classification (Easley, López de Prado, O'Hara 2012) over the ATM CE; honesty enforced by the ablation run.
- Null model: random entries on the engine's own evaluation grid with the identical triple-barrier exit walk and costs.
- DSR per Bailey & López de Prado (2014).