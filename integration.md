# SniperAgent Integration Plan
## SmartAgent (HERMES OMNIHORIZON) × PriceActionAgent (SniperAgent)

> **Goal:** Make both agents operate as one system — PriceActionAgent provides structural conviction, SmartAgent provides statistical regime probability. Neither agent alone has both. The combination does.

---

## 1. Why Integrate

| Capability | SmartAgent | PriceActionAgent | Combined |
|---|---|---|---|
| Named price structure (S/R, VP, Wyckoff) | ✗ | ✓ | ✓ |
| Statistical regime probability | ✓ | ✗ | ✓ |
| Real-time order-flow (PCR, VRP, flow) | ✓ | ✗ | ✓ |
| Self-learning from outcomes | ✓ | ✗ | ✓ |
| Named setup discipline (12 setups) | ✗ | ✓ | ✓ |
| R:R enforcement before entry | partial | ✓ | ✓ |
| Hard disqualifiers (FNO expiry, earnings) | ✗ | ✓ | ✓ |
| Multi-horizon (intraday → positional) | ✓ | swing only | ✓ |

**Core thesis:** PriceActionAgent filters *what* to trade (named setup, structural zone). SmartAgent confirms *when* the regime supports it (Bayesian probability). The order placed is the intersection of both signals.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    EOD PIPELINE  (~18:00 IST)               │
│                                                             │
│  Market data (Upstox) ──► PriceActionAgent                  │
│                               │                             │
│                         claim_registry                      │
│                         named_setup (B1..M2 / NO_TRADE)     │
│                         entry / stop / target               │
│                               │                             │
│                               ▼                             │
│                     IntegratorBridge                        │
│                     (new module: integrator.py)             │
│                               │                             │
│                    ┌──────────┴──────────┐                  │
│                    │                     │                   │
│             p_swing check          x_structure              │
│             (SmartAgent DB)        injection                 │
│                    │                     │                   │
│                    └──────────┬──────────┘                  │
│                               │                             │
│                        GATE DECISION                        │
│                    APPROVED / SUPPRESSED                    │
│                               │                             │
│                    (if APPROVED) ──► OrderQueue             │
│                                        │                    │
│                                  Upstox Orders              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                   INTRADAY  (live, ~09:20 IST)              │
│                                                             │
│  SmartAgent intraday horizon runs autonomously              │
│  PriceActionAgent setup label attached to open swing/pos    │
│  positions for context (no intraday interference)           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                  OUTCOME LOOP  (nightly, ~21:00 IST)        │
│                                                             │
│  SmartAgent exit outcomes ──► SETUP_PARAMS calibrator       │
│                               (updates config.py thresholds │
│                                after N=200 trades per setup) │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Three Integration Points

### 3.1 Gate — SmartAgent approves, PriceActionAgent selects

**What it does:** PriceActionAgent runs first. Its named setup + prices are held in the `OrderQueue`. Before submission, `IntegratorBridge` checks SmartAgent's swing-horizon probability for that symbol.

**Rule:**
```
if pa_decision.setup == "NO_TRADE":
    suppress  # PriceActionAgent already rejected

if smart_agent.p_swing(symbol) < P_GATE_THRESHOLD:   # default 0.55
    suppress + log reason = "regime_gate"

else:
    approve → submit order
```

**Why 0.55:** Neutral Bayesian log-odds = 0.50. Above 0.55 means the 13-feature evidence vector has mild positive expectation in the swing horizon. Anything below 0.55 means SmartAgent sees chop or adverse regime — structural setup quality doesn't matter if the regime doesn't support it.

**Config keys to add to `config.py`:**
```python
INTEGRATION = {
    "p_gate_swing":      float(os.environ.get("INT_P_GATE_SWING",      "0.55")),
    "p_gate_positional": float(os.environ.get("INT_P_GATE_POSITIONAL",  "0.58")),
    "gate_enabled":      os.environ.get("INT_GATE_ENABLED", "1") == "1",
}
```

---

### 3.2 x_structure Injection — PriceActionAgent feeds SmartAgent's evidence vector

**What it does:** SmartAgent's 13-feature vector has no concept of "price is at a named structural zone." PriceActionAgent's `claim_registry` knows exactly where every S/R zone, VP level, and VWAP anchor is. This adds feature `x_structure` (index 13) to SmartAgent's evidence vector.

**Feature definition:**
```python
def compute_x_structure(claim_registry: dict, current_price: float) -> float:
    """
    1.0  = current price within 0.5% of a CR-registered S/R zone (long bias zone)
   -1.0  = within 0.5% of a resistance zone (short bias zone)
    0.0  = no registered zone nearby
    """
    zone_tol = SETUP_PARAMS["sr_zone_tolerance_pct"] / 100   # default 0.005
    for claim in claim_registry.values():
        level = claim.get("price")
        if level and abs(current_price - level) / level <= zone_tol:
            return 1.0 if claim.get("bias") == "support" else -1.0
    return 0.0
```

**Integration point in SmartAgent:** `allstrategy.py` → `_build_evidence_vector()` → append `x_structure` before log-odds computation. The Hedge learner will automatically learn how much weight to assign this feature from outcomes.

**New FEATURES list:**
```python
FEATURES = ["structure", "value", "vol_anom", "mom_t", "vratio",
            "flow", "pcr", "vrp", "maturity", "accel",
            "align", "mom_l", "mom_v",
            "pa_structure"]   # ← new feature from PriceActionAgent
```

---

### 3.3 Outcome Loop — SmartAgent calibrates PriceActionAgent's thresholds

**What it does:** All 33 `SETUP_PARAMS` parameters tagged `[U]` or `[H]` are currently guesses or heuristics. SmartAgent records every trade outcome (R-multiple, horizon, regime, setup label). After 200+ outcomes per setup type, a nightly calibrator runs regression to find the threshold value that maximises expected R-multiple on NSE data.

**Outcome schema (add to SmartAgent DB):**
```sql
CREATE TABLE IF NOT EXISTS pa_outcomes (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL,
    setup       TEXT NOT NULL,   -- B1..M2
    entry       REAL,
    target      REAL,
    stop_loss   REAL,
    exit_price  REAL,
    r_multiple  REAL,            -- (exit - entry) / (entry - stop)
    p_swing_at_entry REAL,
    regime      TEXT,            -- trend / chop (from vratio)
    date        TEXT
);
```

**Calibrator (new script: `calibrate_setup_params.py`):**
```python
def calibrate(setup: str, param: str, values: list[float]) -> float:
    """
    For each candidate value, filter outcomes where the param condition holds,
    compute mean R-multiple. Return the value that maximises it.
    Requires N >= MIN_SAMPLES to avoid overfitting.
    """
    MIN_SAMPLES = 200
    ...
```

**Config update trigger:** Calibrator writes updated values to `config.py`'s `SETUP_PARAMS` dict only when `N >= 200` and the new value differs by >5% from current. All changes are git-committed with the calibration date.

---

## 4. New Module: `integrator.py`

**Location:** `SniperAgent/integrator.py` (top-level, shared by both agents)

**Responsibilities:**
- Load PriceActionAgent synthesis results from its SQLite DB
- Load SmartAgent swing probability for same symbols from its DB
- Apply Gate (3.1)
- Compute `x_structure` from claim_registry and push to SmartAgent (3.2)
- Write approved decisions to `OrderQueue`
- Log every gate decision (approved / suppressed + reason) to `Data/integration_log.db`

**Key function signatures:**
```python
def run_integration_pass(
    trade_date: str,
    gate_threshold: float = INTEGRATION["p_gate_swing"],
) -> list[ApprovedOrder]:
    """
    Called nightly after PriceActionAgent synthesis completes.
    Returns list of orders approved for submission.
    """

def push_outcome(
    symbol: str,
    setup: str,
    exit_price: float,
    r_multiple: float,
) -> None:
    """
    Called by SmartAgent exit handler. Persists to pa_outcomes for calibrator.
    """
```

---

## 5. Data Contracts

### PriceActionAgent → IntegratorBridge

PriceActionAgent's existing `trade_decision` output (already in `synthesis.py`):

```json
{
  "symbol": "RELIANCE",
  "trade_decision": {
    "action":    "BUY",
    "setup":     "B1",
    "entry":     2845.50,
    "target":    2920.00,
    "stop_loss": 2805.00,
    "rejection": null
  },
  "claim_registry": {
    "CR001": {"type": "support", "price": 2843.0, "bias": "support", "tests": 3},
    ...
  }
}
```

No changes needed to PriceActionAgent output. IntegratorBridge reads it as-is.

### SmartAgent → IntegratorBridge

SmartAgent exposes per-horizon probability via:
```python
# allstrategy.py — add this accessor
def get_swing_probability(symbol: str, db_path: str = DB_PATH) -> float | None:
    """Returns latest p_swing for symbol, or None if no recent data."""
    ...
```

### IntegratorBridge → OrderQueue

```python
@dataclass
class ApprovedOrder:
    symbol:       str
    setup:        str          # B1..M2
    action:       str          # BUY / SELL
    entry:        float
    target:       float
    stop_loss:    float
    p_swing:      float        # SmartAgent probability at approval time
    approved_at:  str          # ISO timestamp
```

---

## 6. Phased Rollout

### Phase 1 — Paper mode (Week 1–2)
- Implement `integrator.py` with Gate only (3.1)
- Log every gate decision: what PA decided, what SmartAgent said, what would have been ordered
- **No real orders placed.** Measure: how often does the gate suppress PA trades? Is `p_swing` correlated with PA setup quality?
- Success criterion: gate correlation with next-day returns > 0.1 on 20+ symbols

### Phase 2 — Live Gate (Week 3–4)
- Enable Gate for real orders on 5 liquid symbols (RELIANCE, HDFCBANK, ICICIBANK, SBIN, INFY)
- SmartAgent intraday continues running independently (no interference)
- Track: gate-approved trades vs gate-suppressed trades — which set has better R-multiple?
- Success criterion: gate-approved mean R-multiple > 0.8 on 20+ trades

### Phase 3 — x_structure injection (Month 2)
- Add `pa_structure` as feature x13 to SmartAgent's evidence vector
- Start with weight = 0.0 (Hedge learner discovers it from scratch)
- Monitor: does Hedge learner increase the weight over time? If yes → feature has signal
- Success criterion: `pa_structure` weight > 0.05 after 100 trades (feature being used)

### Phase 4 — Outcome calibration (Month 3+)
- `pa_outcomes` table has 200+ rows per major setup (B1, B2, S1, S2)
- Run `calibrate_setup_params.py` for the first time
- Update [U]-tagged parameters in `SETUP_PARAMS` with calibrated values
- Rerun PriceActionAgent on historical data with new params — compare setup hit rates
- Success criterion: calibrated params produce ≥5% improvement in mean R-multiple vs defaults

---

## 7. Risk Controls

| Risk | Control |
|---|---|
| Integration bug approves bad trade | Phase 1 is paper-only; Gate default is suppress-on-error |
| SmartAgent DB unavailable | Gate fails closed: `p_swing = None` → suppress |
| PriceActionAgent synthesis fails | No output → nothing enters OrderQueue |
| Calibration overfits | Min 200 samples; changes only if >5% delta from current; always git-committed |
| Feature x13 destabilises SmartAgent | Start weight = 0; V2 kill-switch `OMNI_V2=0` restores v1 instantly |
| Both agents agree on wrong trade | Position sizing is still Kelly-constrained by SmartAgent; max loss bounded by stop_loss |

---

## 8. Files to Create / Modify

| File | Action | What changes |
|---|---|---|
| `integrator.py` | **Create** | Gate + x_structure + outcome push |
| `calibrate_setup_params.py` | **Create** | Threshold calibration from pa_outcomes |
| `SmartAgent/allstrategy.py` | **Modify** | Add `pa_structure` to FEATURES; add `get_swing_probability()` accessor |
| `PriceActionAgent/config.py` | **Modify** | Add `INTEGRATION` dict |
| `Data/integration_log.db` | **Auto-created** | Gate decision log |
| `Data/SmartAgent/hermes_omnihorizon_v2.db` | **Modify schema** | Add `pa_outcomes` table |

---

## 9. Success Metrics

After Phase 4 is complete, the combined system should measurably outperform either agent alone on:

- **Strike rate:** % trades with R-multiple > 0 (target: +5% vs PA alone)
- **Mean R-multiple:** average (exit − entry) / (entry − stop) per trade (target: +0.15R vs PA alone)
- **Max drawdown:** largest peak-to-trough in equity curve (target: <15% smaller than SmartAgent alone)
- **Suppression precision:** % gate-suppressed trades that would have been losers (target: >60%)

---

## 10. Open Questions

1. **Symbol universe alignment:** SmartAgent currently runs NSE-100 via `universe_nse100.py`; PriceActionAgent has its own symbol list. These must be reconciled — IntegratorBridge should only process the intersection.
2. **Timing:** PriceActionAgent synthesis runs EOD. SmartAgent swing evaluation also runs EOD. Order of execution matters — PA must complete before integrator runs.
3. **Intraday interference:** SmartAgent intraday sleeve may open a trade on a symbol where PriceActionAgent has said NO_TRADE at EOD. Decision: intraday trades are SmartAgent-only; gate applies only to swing/positional entries initiated from PA output.
4. **FNO expiry handling:** PriceActionAgent already disqualifies trades within `fno_expiry_buffer_days` of expiry. SmartAgent does not. IntegratorBridge should propagate this disqualifier to SmartAgent swing entries too.
5. **Claim registry persistence:** Currently `claim_registry` is embedded in the synthesis JSON. For x_structure injection to work intraday, it needs to be persisted to a queryable store (SQLite or Redis) at EOD and read by SmartAgent during live session.

---

*Last updated: 2026-06-14*
*Status: Planning — Phase 1 not yet started*
