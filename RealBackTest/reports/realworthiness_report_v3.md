# Real-Data Worthiness Report — `v3`
_Generated 2026-06-12T21:15:45.411866+00:00Z · window **2024-06-01 → 2026-06-10** · costs **10.0 bps round-trip** · horizons short_term, swing, positional · symbols RELIANCE, HDFCBANK, ICICIBANK, TCS, SBIN_

## VERDICT: **NO-GO — see failed checks**

| check | result | detail |
|---|---|---|
| Positive mean net R | FAIL | mean R = -0.063 over 49 closed trades |
| Bootstrap p < 0.05 | FAIL | p = 0.6205 |
| Beats random entries (median pctile ≥ 95.0) | FAIL | median pctile = 72.2, worst cell = 7.5 |
| IV recovery ≥ 90% | FAIL | worst symbol = 82.9% |
| Max DD ≤ 10% of capital | FAIL | ₹151,558 vs ₹1,000,000 |
| Deflated Sharpe > 0.5 (prob true SR > 0) | FAIL | DSR = 0.026 |
| Learning not degrading (2nd half ≥ 1st half − 0.1R) | PASS | -0.450 → +0.310 |
| Edge survives flow-proxy ablation (mean R > 0 with x6 = 0) | FAIL | ablated mean R = -0.092 vs main -0.063 |
| Sample size ≥ 30 closed trades | PASS | n = 49 |

## 1. Headline (closed trades, net of costs)
| metric | value |
|---|---|
| closed trades | 49 |
| hit rate | 44.9% |
| mean net R | -0.063 |
| per-trade Sharpe | -0.056 |
| deflated Sharpe (prob) | 0.026 |
| bootstrap p (mean R > 0) | 0.6205 |
| total net PnL | ₹-34,967 |
| max drawdown | ₹151,558 |
| outcomes | {"LOSS": 20, "TRAILED": 14, "TIME_EXIT": 8, "PROFIT": 7} |
| open at end (excluded) | 0 |

## 2. Per-horizon
| horizon | trades | hit | mean R | t-stat | net PnL ₹ | p* end | calib a/b |
|---|---|---|---|---|---|---|---|
| positional | 11 | 36.4% | +0.032 | +0.14 | 2,688 | 0.697 | 0.82/-0.15 |
| short_term | 22 | 50.0% | -0.067 | -0.24 | -18,440 | 0.687 | 0.60/-0.27 |
| swing | 16 | 43.8% | -0.122 | -0.43 | -19,215 | 0.662 | 0.63/-0.23 |

## 3. Strategy vs random-entry null (same exits, same costs)
| cell | n | strategy mean R | null mean | null p95 | strategy pctile |
|---|---|---|---|---|---|
| short_term/ICICIBANK | 5 | -1.015 | -0.309 | +0.584 | 7.5 |
| short_term/RELIANCE | 5 | -0.182 | -0.300 | +0.597 | 57.5 |
| short_term/SBIN | 6 | +0.440 | -0.239 | +0.582 | 91.5 |
| swing/SBIN | 7 | +0.388 | -0.089 | +0.564 | 87.0 |

_Read: pctile is where the strategy's mean R lands in 200 random-entry portfolios of the same size. > 95 means the evidence gating beats luck._

## 4. Buy-and-hold reference
| symbol | total return | ann vol | Sharpe | max DD |
|---|---|---|---|---|
| RELIANCE | -16.7% | 21.9% | -0.72 | 27.4% |
| HDFCBANK | -5.0% | 20.0% | -0.45 | 27.8% |
| ICICIBANK | +11.5% | 19.5% | -0.05 | 19.0% |
| TCS | -41.8% | 23.3% | -1.45 | 52.8% |
| SBIN | +10.8% | 25.3% | -0.05 | 23.9% |

_The agent risks a few % of one sleeve per trade — compare risk-adjusted, not absolute, returns._

## 5. Walk-forward progression (online learning)
| fold | window | trades | mean R | hit | net PnL ₹ |
|---|---|---|---|---|---|
| 1 | 2024-06-03→2024-11-19 | 26 | -0.420 | 23% | -119,126 |
| 2 | 2024-11-19→2025-05-07 | 6 | 0.851 | 83% | 62,786 |
| 3 | 2025-05-07→2025-10-23 | 3 | -0.308 | 33% | -8,792 |
| 4 | 2025-10-23→2026-04-10 | 14 | 0.262 | 71% | 30,165 |

First half mean R **-0.450** → second half **+0.310** (improving).

## 6. Flow-proxy ablation (x6 = 0)
| run | trades | mean R | net PnL ₹ |
|---|---|---|---|
| main (BVC flow proxy) | 49 | -0.063 | -34,967 |
| ablated (flow zeroed) | 46 | -0.092 | -43,539 |

_x6 is the only reconstructed feature without a direct historical counterpart (BVC executed-flow proxy for live depth imbalance). If the edge dies when it is zeroed, the proxy — not the market — was the edge._

## 7. Options-plane reconstruction quality
| symbol | chain calls | served | availability | IV success | parity rescues | stale | no contract |
|---|---|---|---|---|---|---|---|
| RELIANCE | 6458 | 3996 | 61.9% | 100.0% | 19 | 1838 | 624 |
| HDFCBANK | 6442 | 2135 | 33.1% | 82.9% | 7 | 3683 | 624 |
| ICICIBANK | 6452 | 3965 | 61.5% | 100.0% | 18 | 1863 | 624 |

_IV recovered by Black-Scholes inversion of real ATM premiums; PCR from real per-minute OI; flow via BVC. When availability gaps occur the engine degrades to structure-only with the P_STAR_NO_OPTIONS floor — the identical live failure path._

## 8. Gate telemetry (why it didn't trade)
| gate | positional | short_term | swing |
|---|---|---|---|
| align_veto | 0 | 13 | 0 |
| already_open | 307910 | 7050 | 41960 |
| cooldown | 9 | 15 | 6 |
| entered | 11 | 22 | 16 |
| evaluated | 1684 | 32209 | 2397 |
| min_profit | 0 | 538 | 0 |
| p_star | 1664 | 31621 | 2375 |
| shadow_loss | 3 | 26 | 43 |
| shadow_open | 13 | 56 | 84 |
| shadow_win | 9 | 30 | 41 |

## 9. Learning evolution (Δ weight, earned credit)

**short_term**
| feature | Δw | credit |
|---|---|---|
| structure | +0.040 | +0.211 |
| value | -0.025 | -0.006 |
| vol_anom | +0.086 | +0.437 |
| mom_t | +0.022 | +0.166 |
| vratio | -0.027 | -0.031 |
| flow | -0.045 | -0.087 |
| pcr | -0.043 | -0.080 |
| vrp | -0.011 | +0.043 |
| maturity | -0.010 | +0.041 |
| accel | +0.024 | +0.211 |
| align | -0.014 | +0.039 |
| mom_l | -0.045 | -0.089 |
| mom_v | +0.048 | +0.323 |

**swing**
| feature | Δw | credit |
|---|---|---|
| structure | +0.001 | -0.003 |
| value | +0.007 | +0.017 |
| vol_anom | -0.032 | -0.178 |
| mom_t | -0.006 | -0.024 |
| vratio | +0.001 | -0.004 |
| flow | +0.006 | +0.025 |
| pcr | +0.002 | -0.000 |
| vrp | +0.003 | +0.007 |
| maturity | +0.010 | +0.041 |
| accel | -0.003 | -0.023 |
| align | +0.015 | +0.048 |
| mom_l | +0.012 | +0.037 |
| mom_v | -0.016 | -0.090 |

**positional**
| feature | Δw | credit |
|---|---|---|
| structure | +0.007 | +0.052 |
| value | +0.003 | +0.037 |
| vol_anom | +0.004 | +0.051 |
| mom_t | +0.007 | +0.040 |
| vratio | -0.016 | -0.019 |
| flow | -0.004 | -0.015 |
| pcr | -0.004 | +0.000 |
| vrp | -0.004 | +0.001 |
| maturity | -0.012 | -0.038 |
| accel | +0.005 | +0.052 |
| align | +0.001 | +0.030 |
| mom_l | +0.005 | +0.045 |
| mom_v | +0.007 | +0.059 |

## 10. Data audit
```json
{
  "symbols": {
    "RELIANCE": {
      "minutes/1": {
        "bars": 187245,
        "sessions": 501,
        "first": "2024-06-03 09:15:00+05:30",
        "last": "2026-06-10 15:29:00+05:30",
        "bars_per_session": 373.7
      },
      "minutes/30": {
        "bars": 6886,
        "sessions": 532,
        "first": "2024-04-18 09:15:00+05:30",
        "last": "2026-06-10 15:15:00+05:30",
        "bars_per_session": 12.9
      },
      "days/1": {
        "bars": 805,
        "sessions": 805,
        "first": "2023-03-09 00:00:00+05:30",
        "last": "2026-06-10 00:00:00+05:30",
        "bars_per_session": 1.0
      },
      "weeks/1": {
        "bars": 264,
        "sessions": 264,
        "first": "2021-05-24 00:00:00+05:30",
        "last": "2026-06-08 00:00:00+05:30",
        "bars_per_session": 1.0
      },
      "options": {
        "contracts": 2026,
        "expiries": 34,
        "option_bars": 4779998
      }
    },
    "HDFCBANK": {
      "minutes/1": {
        "bars": 187245,
        "sessions": 501,
        "first": "2024-06-03 09:15:00+05:30",
        "last": "2026-06-10 15:29:00+05:30",
        "bars_per_session": 373.7
      },
      "minutes/30": {
        "bars": 6886,
        "sessions": 532,
        "first": "2024-04-18 09:15:00+05:30",
        "last": "2026-06-10 15:15:00+05:30",
        "bars_per_session": 12.9
      },
      "days/1": {
        "bars": 805,
        "sessions": 805,
        "first": "2023-03-09 00:00:00+05:30",
        "last": "2026-06-10 00:00:00+05:30",
        "bars_per_session": 1.0
      },
      "weeks/1": {
        "bars": 264,
        "sessions": 264,
        "first": "2021-05-24 00:00:00+05:30",
        "last": "2026-06-08 00:00:00+05:30",
        "bars_per_session": 1.0
      },
      "options": {
        "contracts": 1881,
        "expiries": 34,
        "option_bars": 2110726
      }
    },
    "ICICIBANK": {
      "minutes/1": {
        "bars": 187245,
        "sessions": 501,
        "first": "2024-06-03 09:15:00+05:30",
        "last": "2026-06-10 15:29:00+05:30",
        "bars_per_session": 373.7
      },
      "minutes/30": {
        "bars": 6886,
        "sessions": 532,
        "first": "2024-04-18 09:15:00+05:30",
        "last": "2026-06-10 15:15:00+05:30",
        "bars_per_session": 12.9
      },
      "days/1": {
        "bars": 805,
        "sessions": 805,
        "first": "2023-03-09 00:00:00+05:30",
        "last": "2026-06-10 00:00:00+05:30",
        "bars_per_session": 1.0
      },
      "weeks/1": {
        "bars": 264,
        "sessions": 264,
        "first": "2021-05-24 00:00:00+05:30",
        "last": "2026-06-08 00:00:00+05:30",
        "bars_per_session": 1.0
      },
      "options": {
        "contracts": 1791,
        "expiries": 34,
        "option_bars": 3780944
      }
    },
    "TCS": {
      "minutes/1": {
        "bars": 187245,
        "sessions": 501,
        "first": "2024-06-03 09:15:00+05:30",
        "last": "2026-06-10 15:29:00+05:30",
        "bars_per_session": 373.7
      },
      "minutes/30": {
        "bars": 6886,
        "sessions": 532,
        "first": "2024-04-18 09:15:00+05:30",
        "last": "2026-06-10 15:15:00+05:30",
        "bars_per_session": 12.9
      },
      "days/1": {
        "bars": 805,
        "sessions": 805,
        "first": "2023-03-09 00:00:00+05:30",
        "last": "2026-06-10 00:00:00+05:30",
        "bars_per_session": 1.0
      },
      "weeks/1": {
        "bars": 264,
        "sessions": 264,
        "first": "2021-05-24 00:00:00+05:30",
        "last": "2026-06-08 00:00:00+05:30",
        "bars_per_session": 1.0
      },
      "options": {
        "contracts": 0,
        "expiries": 0,
        "option_bars": 0
      }
    },
    "SBIN": {
      "minutes/1": {
        "bars": 187245,
        "sessions": 501,
        "first": "2024-06-03 09:15:00+05:30",
        "last": "2026-06-10 15:29:00+05:30",
        "bars_per_session": 373.7
      },
      "minutes/30": {
        "bars": 6886,
        "sessions": 532,
        "first": "2024-04-18 09:15:00+05:30",
        "last": "2026-06-10 15:15:00+05:30",
        "bars_per_session": 12.9
      },
      "days/1": {
        "bars": 805,
        "sessions": 805,
        "first": "2023-03-09 00:00:00+05:30",
        "last": "2026-06-10 00:00:00+05:30",
        "bars_per_session": 1.0
      },
      "weeks/1": {
        "bars": 264,
        "sessions": 264,
        "first": "2021-05-24 00:00:00+05:30",
        "last": "2026-06-08 00:00:00+05:30",
        "bars_per_session": 1.0
      },
      "options": {
        "contracts": 0,
        "expiries": 0,
        "option_bars": 0
      }
    }
  },
  "window": {
    "start": "2024-06-01",
    "end": "2026-06-10"
  },
  "options_ok": {
    "RELIANCE": true,
    "HDFCBANK": true,
    "ICICIBANK": true,
    "TCS": false,
    "SBIN": false
  }
}
```

## Method notes
- Engine under test: `SmartAgent/allstrategy.py`, unmodified — clock, bar feed and chain feed patched; fusion, calibration, gates, Kelly, exits and learning untouched.
- No lookahead: 30-min bars visible only after close, daily bars next day, weekly bars next week; options joined backward as-of with a 15-min staleness cap.
- IV by Brent inversion of Black-Scholes on real premiums (r = 6.5%); put-call-parity fallback; EWMA smoothing with jump filter.
- Flow x6 via Bulk Volume Classification (Easley, López de Prado, O'Hara 2012) over the ATM CE; honesty enforced by the ablation run.
- Null model: random entries on the engine's own evaluation grid with the identical triple-barrier exit walk and costs.
- DSR per Bailey & López de Prado (2014).