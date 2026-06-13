# Real-Data Worthiness Report — `v2`
_Generated 2026-06-12T19:48:37.156308+00:00Z · window **2024-06-01 → 2026-06-10** · costs **10.0 bps round-trip** · horizons short_term, swing, positional · symbols RELIANCE, HDFCBANK, ICICIBANK, TCS, SBIN_

## VERDICT: **NO-GO — see failed checks**

| check | result | detail |
|---|---|---|
| Positive mean net R | FAIL | mean R = -0.096 over 61 closed trades |
| Bootstrap p < 0.05 | FAIL | p = 0.7687 |
| Beats random entries (median pctile ≥ 95.0) | FAIL | median pctile = 80.2, worst cell = 29.5 |
| IV recovery ≥ 90% | FAIL | worst symbol = 82.3% |
| Max DD ≤ 10% of capital | FAIL | ₹119,946 vs ₹1,000,000 |
| Deflated Sharpe > 0.5 (prob true SR > 0) | FAIL | DSR = 0.014 |
| Learning not degrading (2nd half ≥ 1st half − 0.1R) | PASS | -0.272 → +0.074 |
| Edge survives flow-proxy ablation (mean R > 0 with x6 = 0) | FAIL | ablated mean R = -0.082 vs main -0.096 |
| Sample size ≥ 30 closed trades | PASS | n = 61 |

## 1. Headline (closed trades, net of costs)
| metric | value |
|---|---|
| closed trades | 61 |
| hit rate | 47.5% |
| mean net R | -0.096 |
| per-trade Sharpe | -0.088 |
| deflated Sharpe (prob) | 0.014 |
| bootstrap p (mean R > 0) | 0.7687 |
| total net PnL | ₹-68,493 |
| max drawdown | ₹119,946 |
| outcomes | {"LOSS": 26, "TRAILED": 21, "PROFIT": 8, "TIME_EXIT": 6} |
| open at end (excluded) | 0 |

## 2. Per-horizon
| horizon | trades | hit | mean R | t-stat | net PnL ₹ | p* end | calib a/b |
|---|---|---|---|---|---|---|---|
| positional | 8 | 37.5% | +0.106 | +0.41 | 6,377 | 0.685 | 0.86/-0.10 |
| short_term | 36 | 52.8% | -0.088 | -0.45 | -39,857 | 0.695 | 0.51/-0.40 |
| swing | 17 | 41.2% | -0.208 | -0.78 | -35,014 | 0.619 | 0.59/-0.20 |

## 3. Strategy vs random-entry null (same exits, same costs)
| cell | n | strategy mean R | null mean | null p95 | strategy pctile |
|---|---|---|---|---|---|
| short_term/HDFCBANK | 5 | -0.604 | -0.339 | +0.622 | 29.5 |
| short_term/ICICIBANK | 7 | -0.320 | -0.392 | +0.393 | 56.0 |
| short_term/RELIANCE | 8 | +0.004 | -0.354 | +0.464 | 79.5 |
| short_term/SBIN | 9 | +0.045 | -0.262 | +0.293 | 81.0 |
| short_term/TCS | 7 | +0.234 | -0.348 | +0.432 | 90.5 |
| swing/SBIN | 7 | +0.316 | -0.089 | +0.564 | 82.0 |

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
| 1 | 2024-06-03→2024-11-19 | 31 | -0.252 | 39% | -80,361 |
| 2 | 2024-11-19→2025-05-06 | 6 | 0.623 | 67% | 46,632 |
| 3 | 2025-05-06→2025-10-22 | 4 | -0.321 | 25% | -11,475 |
| 4 | 2025-10-22→2026-04-09 | 20 | -0.026 | 60% | -23,289 |

First half mean R **-0.272** → second half **+0.074** (improving).

## 6. Flow-proxy ablation (x6 = 0)
| run | trades | mean R | net PnL ₹ |
|---|---|---|---|
| main (BVC flow proxy) | 61 | -0.096 | -68,493 |
| ablated (flow zeroed) | 56 | -0.082 | -56,647 |

_x6 is the only reconstructed feature without a direct historical counterpart (BVC executed-flow proxy for live depth imbalance). If the edge dies when it is zeroed, the proxy — not the market — was the edge._

## 7. Options-plane reconstruction quality
| symbol | chain calls | served | availability | IV success | parity rescues | stale | no contract |
|---|---|---|---|---|---|---|---|
| RELIANCE | 6302 | 3860 | 61.3% | 100.0% | 19 | 1818 | 624 |
| HDFCBANK | 6365 | 2059 | 32.4% | 82.3% | 7 | 3682 | 624 |
| ICICIBANK | 6417 | 3935 | 61.3% | 100.0% | 18 | 1858 | 624 |

_IV recovered by Black-Scholes inversion of real ATM premiums; PCR from real per-minute OI; flow via BVC. When availability gaps occur the engine degrades to structure-only with the P_STAR_NO_OPTIONS floor — the identical live failure path._

## 8. Gate telemetry (why it didn't trade)
| gate | positional | short_term | swing |
|---|---|---|---|
| already_open | 206359 | 18127 | 43820 |
| cooldown | 5 | 39 | 2 |
| entered | 8 | 36 | 17 |
| evaluated | 1954 | 31825 | 2392 |
| min_profit | 0 | 1146 | 0 |
| p_star | 1941 | 30604 | 2373 |
| shadow_loss | 7 | 28 | 42 |
| shadow_open | 18 | 61 | 86 |
| shadow_win | 10 | 33 | 44 |

## 9. Learning evolution (Δ weight, earned credit)

**short_term**
| feature | Δw | credit |
|---|---|---|
| structure | +0.128 | +0.379 |
| value | -0.065 | -0.033 |
| vol_anom | +0.128 | +0.472 |
| mom_t | +0.095 | +0.337 |
| vratio | -0.043 | +0.009 |
| flow | -0.050 | +0.008 |
| pcr | -0.125 | -0.203 |
| vrp | -0.061 | -0.047 |
| maturity | -0.106 | -0.253 |
| accel | +0.100 | +0.445 |

**swing**
| feature | Δw | credit |
|---|---|---|
| structure | +0.017 | +0.015 |
| value | +0.023 | +0.034 |
| vol_anom | -0.027 | -0.115 |
| mom_t | -0.033 | -0.078 |
| vratio | +0.020 | +0.017 |
| flow | -0.012 | -0.073 |
| pcr | +0.008 | -0.001 |
| vrp | +0.009 | +0.002 |
| maturity | +0.020 | +0.041 |
| accel | -0.024 | -0.104 |

**positional**
| feature | Δw | credit |
|---|---|---|
| structure | +0.015 | +0.062 |
| value | +0.013 | +0.063 |
| vol_anom | +0.021 | +0.106 |
| mom_t | +0.024 | +0.059 |
| vratio | -0.026 | -0.018 |
| flow | -0.008 | -0.030 |
| pcr | -0.006 | +0.000 |
| vrp | -0.007 | +0.001 |
| maturity | -0.023 | -0.054 |
| accel | -0.003 | +0.015 |

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
        "option_bars": 3911602
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
        "option_bars": 1622652
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
        "option_bars": 3009075
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