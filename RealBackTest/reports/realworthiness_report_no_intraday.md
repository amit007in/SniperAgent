# Real-Data Worthiness Report — `no_intraday`
_Generated 2026-06-12T18:53:58.702012+00:00Z · window **2024-06-01 → 2026-06-10** · costs **10.0 bps round-trip** · horizons short_term, swing, positional · symbols RELIANCE, HDFCBANK, ICICIBANK, TCS, SBIN_

## VERDICT: **NO-GO — see failed checks**

| check | result | detail |
|---|---|---|
| Positive mean net R | FAIL | mean R = -0.158 over 36 closed trades |
| Bootstrap p < 0.05 | FAIL | p = 0.9147 |
| Beats random entries | N/A | too few trades per cell |
| IV recovery ≥ 90% | FAIL | worst symbol = 82.9% |
| Max DD ≤ 10% of capital | PASS | ₹93,172 vs ₹1,000,000 |
| Deflated Sharpe > 0.5 (prob true SR > 0) | FAIL | DSR = 0.006 |
| Learning not degrading (2nd half ≥ 1st half − 0.1R) | PASS | -0.132 → -0.183 |
| Edge survives flow-proxy ablation (mean R > 0 with x6 = 0) | FAIL | ablated mean R = -0.165 vs main -0.158 |
| Sample size ≥ 30 closed trades | PASS | n = 36 |

## 1. Headline (closed trades, net of costs)
| metric | value |
|---|---|
| closed trades | 36 |
| hit rate | 44.4% |
| mean net R | -0.158 |
| per-trade Sharpe | -0.185 |
| deflated Sharpe (prob) | 0.006 |
| bootstrap p (mean R > 0) | 0.9147 |
| total net PnL | ₹-67,744 |
| max drawdown | ₹93,172 |
| outcomes | {"TRAILED": 18, "LOSS": 11, "TIME_EXIT": 5, "PROFIT": 2} |
| open at end (excluded) | 0 |

## 2. Per-horizon
| horizon | trades | hit | mean R | t-stat | net PnL ₹ | p* end | calib a/b |
|---|---|---|---|---|---|---|---|
| positional | 14 | 57.1% | +0.056 | +0.34 | 6,012 | 0.750 | 0.84/-0.12 |
| short_term | 14 | 35.7% | -0.263 | -1.11 | -45,939 | 0.790 | 0.47/-0.25 |
| swing | 8 | 37.5% | -0.349 | -0.85 | -27,818 | 0.760 | 0.71/-0.16 |

## 3. Strategy vs random-entry null (same exits, same costs)
_Too few trades per (horizon, symbol) cell._

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
| 1 | 2024-06-03→2024-11-03 | 30 | -0.215 | 43% | -68,818 |
| 2 | 2024-11-03→2025-04-05 | 0 | — | — | 0 |
| 3 | 2025-04-05→2025-09-06 | 3 | -0.680 | 0% | -21,222 |
| 4 | 2025-09-06→2026-02-06 | 3 | 0.935 | 100% | 22,296 |

First half mean R **-0.132** → second half **-0.183** (degrading).

## 6. Flow-proxy ablation (x6 = 0)
| run | trades | mean R | net PnL ₹ |
|---|---|---|---|
| main (BVC flow proxy) | 36 | -0.158 | -67,744 |
| ablated (flow zeroed) | 36 | -0.165 | -69,543 |

_x6 is the only reconstructed feature without a direct historical counterpart (BVC executed-flow proxy for live depth imbalance). If the edge dies when it is zeroed, the proxy — not the market — was the edge._

## 7. Options-plane reconstruction quality
| symbol | chain calls | served | availability | IV success | parity rescues | stale | no contract |
|---|---|---|---|---|---|---|---|
| RELIANCE | 6472 | 4006 | 61.9% | 100.0% | 19 | 1842 | 624 |
| HDFCBANK | 6481 | 2135 | 32.9% | 82.9% | 7 | 3722 | 624 |
| ICICIBANK | 6479 | 3989 | 61.6% | 100.0% | 18 | 1866 | 624 |

_IV recovered by Black-Scholes inversion of real ATM premiums; PCR from real per-minute OI; flow via BVC. When availability gaps occur the engine degrades to structure-only with the P_STAR_NO_OPTIONS floor — the identical live failure path._

## 8. Gate telemetry (why it didn't trade)
| gate | positional | short_term | swing |
|---|---|---|---|
| already_open | 272742 | 2258 | 17162 |
| cooldown | 22 | 15 | 1 |
| entered | 14 | 14 | 8 |
| evaluated | 1778 | 32378 | 2461 |
| min_profit | 0 | 62 | 0 |
| p_star | 1742 | 32287 | 2452 |

## 9. Learning evolution (Δ weight, earned credit)

**short_term**
| feature | Δw | credit |
|---|---|---|
| structure | -0.072 | -0.122 |
| value | -0.064 | -0.130 |
| vol_anom | -0.037 | -0.101 |
| mom_t | -0.058 | -0.114 |
| vratio | +0.049 | +0.021 |
| flow | +0.041 | +0.003 |
| pcr | +0.101 | +0.071 |
| vrp | +0.040 | +0.009 |

**swing**
| feature | Δw | credit |
|---|---|---|
| structure | -0.073 | -0.142 |
| value | -0.079 | -0.162 |
| vol_anom | -0.043 | -0.133 |
| mom_t | +0.012 | -0.047 |
| vratio | +0.071 | +0.011 |
| flow | +0.030 | +0.000 |
| pcr | +0.041 | +0.000 |
| vrp | +0.041 | +0.000 |

**positional**
| feature | Δw | credit |
|---|---|---|
| structure | +0.016 | +0.047 |
| value | +0.015 | +0.049 |
| vol_anom | -0.009 | +0.009 |
| mom_t | +0.065 | +0.073 |
| vratio | -0.039 | -0.006 |
| flow | -0.015 | -0.022 |
| pcr | -0.014 | +0.000 |
| vrp | -0.019 | -0.004 |

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