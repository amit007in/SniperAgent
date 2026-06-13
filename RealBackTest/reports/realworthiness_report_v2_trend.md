# Real-Data Worthiness Report — `v2_trend`
_Generated 2026-06-12T20:12:07.099705+00:00Z · window **2024-06-01 → 2026-06-10** · costs **10.0 bps round-trip** · horizons short_term, swing, positional · symbols RELIANCE, HDFCBANK, ICICIBANK, TCS, SBIN_

## VERDICT: **NO-GO — see failed checks**

| check | result | detail |
|---|---|---|
| Positive mean net R | FAIL | mean R = -0.000 over 110 closed trades |
| Bootstrap p < 0.05 | FAIL | p = 0.4946 |
| Beats random entries (median pctile ≥ 95.0) | FAIL | median pctile = 53.5, worst cell = 30.0 |
| IV recovery ≥ 90% | FAIL | worst symbol = 82.6% |
| Max DD ≤ 10% of capital | PASS | ₹97,849 vs ₹1,000,000 |
| Deflated Sharpe > 0.5 (prob true SR > 0) | FAIL | DSR = 0.057 |
| Learning not degrading (2nd half ≥ 1st half − 0.1R) | FAIL | +0.066 → -0.066 |
| Edge survives flow-proxy ablation (mean R > 0 with x6 = 0) | FAIL | ablated mean R = -0.022 vs main -0.000 |
| Sample size ≥ 30 closed trades | PASS | n = 110 |

## 1. Headline (closed trades, net of costs)
| metric | value |
|---|---|
| closed trades | 110 |
| hit rate | 50.0% |
| mean net R | -0.000 |
| per-trade Sharpe | -0.000 |
| deflated Sharpe (prob) | 0.057 |
| bootstrap p (mean R > 0) | 0.4946 |
| total net PnL | ₹-5,975 |
| max drawdown | ₹97,849 |
| outcomes | {"LOSS": 48, "TRAILED": 34, "PROFIT": 17, "TIME_EXIT": 11} |
| open at end (excluded) | 0 |

## 2. Per-horizon
| horizon | trades | hit | mean R | t-stat | net PnL ₹ | p* end | calib a/b |
|---|---|---|---|---|---|---|---|
| positional | 17 | 41.2% | +0.038 | +0.15 | 4,686 | 0.700 | 0.50/-0.26 |
| short_term | 43 | 53.5% | -0.040 | -0.22 | -21,344 | 0.691 | 0.51/-0.50 |
| swing | 50 | 50.0% | +0.020 | +0.13 | 10,683 | 0.700 | 0.50/-0.58 |

## 3. Strategy vs random-entry null (same exits, same costs)
| cell | n | strategy mean R | null mean | null p95 | strategy pctile |
|---|---|---|---|---|---|
| short_term/HDFCBANK | 5 | -0.290 | -0.339 | +0.622 | 53.5 |
| short_term/ICICIBANK | 11 | -0.496 | -0.339 | +0.233 | 36.5 |
| short_term/RELIANCE | 8 | +0.429 | -0.354 | +0.464 | 94.0 |
| short_term/SBIN | 11 | +0.226 | -0.206 | +0.344 | 88.5 |
| short_term/TCS | 8 | -0.088 | -0.304 | +0.399 | 72.0 |
| swing/HDFCBANK | 12 | -0.121 | -0.068 | +0.402 | 44.0 |
| swing/ICICIBANK | 14 | -0.173 | -0.075 | +0.432 | 37.5 |
| swing/RELIANCE | 6 | -0.411 | -0.181 | +0.436 | 30.0 |
| swing/SBIN | 15 | +0.584 | -0.006 | +0.372 | 99.0 |

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
| 1 | 2024-06-03→2024-11-19 | 54 | 0.033 | 54% | 10,539 |
| 2 | 2024-11-19→2025-05-07 | 16 | 0.185 | 56% | 41,437 |
| 3 | 2025-05-07→2025-10-23 | 13 | -0.067 | 38% | -9,397 |
| 4 | 2025-10-23→2026-04-10 | 27 | -0.145 | 44% | -48,554 |

First half mean R **+0.066** → second half **-0.066** (degrading).

## 6. Flow-proxy ablation (x6 = 0)
| run | trades | mean R | net PnL ₹ |
|---|---|---|---|
| main (BVC flow proxy) | 110 | -0.000 | -5,975 |
| ablated (flow zeroed) | 102 | -0.022 | -33,904 |

_x6 is the only reconstructed feature without a direct historical counterpart (BVC executed-flow proxy for live depth imbalance). If the edge dies when it is zeroed, the proxy — not the market — was the edge._

## 7. Options-plane reconstruction quality
| symbol | chain calls | served | availability | IV success | parity rescues | stale | no contract |
|---|---|---|---|---|---|---|---|
| RELIANCE | 6403 | 3959 | 61.8% | 100.0% | 19 | 1820 | 624 |
| HDFCBANK | 6415 | 2123 | 33.1% | 82.6% | 7 | 3666 | 626 |
| ICICIBANK | 6400 | 3922 | 61.3% | 100.0% | 18 | 1854 | 624 |

_IV recovered by Black-Scholes inversion of real ATM premiums; PCR from real per-minute OI; flow via BVC. When availability gaps occur the engine degrades to structure-only with the P_STAR_NO_OPTIONS floor — the identical live failure path._

## 8. Gate telemetry (why it didn't trade)
| gate | positional | short_term | swing |
|---|---|---|---|
| already_open | 526874 | 17607 | 190351 |
| cooldown | 24 | 67 | 24 |
| entered | 17 | 43 | 50 |
| entered_misaligned | 0 | 10 | 0 |
| evaluated | 1098 | 31846 | 2003 |
| min_profit | 0 | 1938 | 0 |
| p_star | 1057 | 29798 | 1929 |
| shadow_loss | 3 | 24 | 31 |
| shadow_open | 8 | 55 | 68 |
| shadow_win | 4 | 31 | 37 |

## 9. Learning evolution (Δ weight, earned credit)

**short_term**
| feature | Δw | credit |
|---|---|---|
| structure | +0.178 | +0.529 |
| value | -0.181 | -0.312 |
| vol_anom | +0.226 | +0.761 |
| mom_t | +0.097 | +0.407 |
| vratio | -0.059 | +0.027 |
| flow | -0.120 | -0.124 |
| pcr | -0.135 | -0.169 |
| vrp | -0.065 | +0.006 |
| maturity | -0.038 | +0.069 |
| accel | +0.141 | +0.625 |
| align | -0.044 | +0.089 |
| mom_l | -0.079 | -0.006 |
| mom_v | +0.080 | +0.453 |

**swing**
| feature | Δw | credit |
|---|---|---|
| structure | +0.187 | +0.438 |
| value | +0.065 | +0.217 |
| vol_anom | -0.013 | +0.017 |
| mom_t | -0.096 | -0.106 |
| vratio | -0.095 | -0.140 |
| flow | -0.021 | -0.023 |
| pcr | -0.040 | -0.057 |
| vrp | -0.020 | +0.004 |
| maturity | -0.034 | -0.057 |
| accel | -0.030 | -0.043 |
| align | +0.081 | +0.254 |
| mom_l | +0.041 | +0.162 |
| mom_v | -0.022 | -0.013 |

**positional**
| feature | Δw | credit |
|---|---|---|
| structure | +0.028 | +0.081 |
| value | +0.004 | +0.024 |
| vol_anom | -0.016 | -0.054 |
| mom_t | +0.006 | +0.022 |
| vratio | +0.001 | +0.014 |
| flow | -0.006 | -0.026 |
| pcr | -0.003 | +0.000 |
| vrp | -0.009 | -0.017 |
| maturity | -0.002 | +0.005 |
| accel | +0.012 | +0.052 |
| align | -0.011 | -0.014 |
| mom_l | +0.006 | +0.028 |
| mom_v | -0.009 | -0.019 |

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