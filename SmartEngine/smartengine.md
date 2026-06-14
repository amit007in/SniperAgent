# SmartEngine — Deterministic + Statistical Replacement for the LLM in PriceActionAgent

**Goal.** Replace the per-symbol LLM call in `PriceActionAgent` with a deterministic,
statistically-calibrated engine that emits the **exact same JSON schema**, so the rest of
the pipeline (`synthesis.py`, `pa_store.py`, the DB, downstream consumers) works unchanged.
A single config switch (`PA_DECISION_ENGINE = llm | smart`) toggles between the two. The
SmartEngine must produce results that are **equal or better** than the LLM on realized
trade outcomes, at **near-zero marginal cost** and **millisecond latency**.

This is achievable because the existing prompt is already a near-complete specification of a
rule system: 12 named setups with numeric thresholds, an explicit 1W→1D→4H cascade, hard
disqualifiers, and measured-move target math. The LLM does three things — mechanical
computation, rule evaluation, and fuzzy judgment. Only the third genuinely needs learning,
and that is handled here with classical statistical models, not an LLM.

---

## 0. Relationship to SmartAgent / Hermes Omnihorizon (read first)

A sophisticated quant engine already exists in `SmartAgent/allstrategy.py` — **Hermes
Omnihorizon**, a live, multi-horizon Bayesian confluence engine with online learning,
options flow, fractional-Kelly sizing, and triple-barrier exits. SmartEngine **does not
rebuild any of that math** — it reuses it.

**The two systems solve different problems and are complementary:**

| | Hermes (`SmartAgent/allstrategy.py`) | SmartEngine (this plan) |
|---|---|---|
| Purpose | Standalone live signal generator | Drop-in replacement for the LLM **inside PriceActionAgent** |
| Output | Own signals + Kelly size + exits | Byte-identical PriceActionAgent JSON schema |
| Method | Pure evidence-fusion (no named setups) | PA's 12 named setups + claim registry + cascade, scored statistically |
| Data | Live intraday + options (PCR/IV/VRP) | EOD cash OHLCV + VWAP/VP/Nifty RS |
| Learning | Online exponentiated-gradient, dual-book MoE, Platt calib | Same learner, reused (see below) |
| Maturity | Built, running, self-tuning | Design → build |

**Division of labour.** SmartEngine contributes the piece Hermes lacks — the
**PA-taxonomy rule layer** (12 setups, claim registry, 1W→1D→4H cascade) and the
**schema serializer + LLM toggle**. Hermes contributes the **statistical brain** — the
feature math, the fusion, the calibrated online learner. SmartEngine imports these directly:

| SmartEngine module | Reuses from `SmartAgent/allstrategy.py` |
|---|---|
| `features.py` | `wilder_atr`, `volume_zscore`, `momentum_tstat`, `variance_ratio`, `volume_profile`, `ewma_realised_vol` |
| `regime.py` | `variance_ratio` (trend vs mean-revert), `momentum_tstat` (drift quality); HMM added only if these prove insufficient |
| `scorer.py` | `sigmoid`, `kelly_fraction` now; `HorizonLearner` **later, optional** (see dependency note) |

**⚠️ Dependency split — Hermes *learning* is in progress and is NOT on SmartEngine's critical
path.** Reuse divides into two classes:

- **Stable, reuse now:** the pure stateless feature functions above (`variance_ratio`,
  `momentum_tstat`, `volume_profile`, `wilder_atr`, `volume_zscore`, `sigmoid`,
  `kelly_fraction`). These are complete and have nothing to do with learning.
- **In-progress, do NOT depend on for ship:** `HorizonLearner` (its online learning is
  still being validated). SmartEngine must be able to fully replace the LLM **without it.**

The mechanism is a **pluggable `Scorer` interface** (§5.6). SmartEngine ships with a
deterministic scorer, then its own self-contained learned scorer, and only adopts Hermes's
`HorizonLearner` as a drop-in **after** Hermes's learning is proven. The "logistic regression"
option is therefore retained as SmartEngine's *independent* learned scorer — it does not block
on Hermes.

### 0.1 Horizon (Hermes) vs Layer (PA) — why the 3-vs-4 mismatch is a non-issue

Hermes runs **4 parallel horizons** (intraday/short/swing/positional), each an *independent
trader* with its own entry/hold/exit/learner, loosely coupled by the bounded κ bonus. PA runs
**3 layers** (1W/1D/4H) that are *hierarchical context for a single decision* — the cascade
(1W→1D→4H) collapses them into ONE evidence vector before any scoring.

Therefore SmartEngine **does not map PA layers onto Hermes horizons.** It uses exactly **one
`HorizonLearner` instance** (swing/daily config, or a dedicated `"pa"` config) fed the single
post-cascade evidence vector. The horizon count is irrelevant.

**Verified reuse boundary (confirmed against `allstrategy.py`):**

| Component | Reuse | Reason |
|---|---|---|
| `variance_ratio`, `momentum_tstat`, `volume_profile`, `wilder_atr`, `volume_zscore`, `sigmoid`, `kelly_fraction` | ✅ | horizon-agnostic pure functions |
| `HorizonLearner` (single instance) | ✅ | `learn()` is "THIS horizon's books only" (line 872) — self-contained; no peer dependency |
| `OmniBrain`, `coupled_log_odds`, `evaluate_entry`, `KAPPA` | ❌ | 4-horizon orchestration (line 1105); SmartEngine's cascade replaces it |

**Integration tasks when feeding the learner (not blockers):**
- Map PA evidence onto a `phi` dict keyed by `FEATURES`; cash-only → `flow/pcr/vrp = 0`
  (Hermes's structure-only mode). Add slots for PA-specific signals (false_breakdown,
  vwap_pos, nifty_rs, gap_type) in `FEATURES`/`V2_FEATURE_PRIORS`.
- Supply `r_multiple`, `g_entry` (regime gate from `variance_ratio`), `L_entry` to `learn()`
  from the labeling/backtest step.

> **Refactor note.** To avoid a fragile cross-package import, extract the pure helpers
> (`wilder_atr`, `momentum_tstat`, `variance_ratio`, `volume_profile`, `volume_zscore`,
> `sigmoid`, `kelly_fraction`) and `HorizonLearner` into a shared module
> (e.g. `SmartAgent/quantcore.py`) that both Hermes and SmartEngine import. No behaviour
> change to Hermes; SmartEngine depends on the shared core, not the live trading file.

**Strategic choice (decide before Phase 2).** Two viable end-states:
1. **Faithful port (recommended).** SmartEngine = exact LLM replacement; PA keeps its
   interpretable named setups and schema; statistical primitives come from Hermes.
2. **Converge.** Make PA a thin adapter over Hermes signals. Less duplication, but loses the
   named-setup interpretability that is PriceActionAgent's reason for existing.
This plan assumes path (1).

---

## 1. Design principles

1. **Schema-identical output.** The engine's `dict` must be byte-compatible with what
   `_parse_json()` returns today. `_persist()` must not be able to tell the difference.
2. **No look-ahead.** Every computation at bar *t* uses only data ≤ *t*. Enforced by
   construction (chronological single pass), not by instruction.
3. **Determinism first, statistics second.** ~90% of decisions come from exact rules.
   Statistical models only resolve the fuzzy ~10% (regime ambiguity, evidence weighting).
4. **Reuse existing deterministic code.** `analytics.py` already computes VWAP, Volume
   Profile (POC/VAH/VAL/HVN/LVN) and Nifty RS in pure Python. The engine consumes these
   directly — they are *already* the "anchor enrichments" the LLM was reading.
5. **Self-learning loop.** All tunable weights/thresholds are fit from the RealBacktest
   outcome labels and retrained as new trades resolve — satisfying the SniperAgent mandate.
6. **Explainability.** Every decision carries the setup id, the per-signal contributions,
   and a templated narrative — at least as auditable as the LLM's prose, and reproducible.

---

## 2. Target output contract (must match exactly)

The active path is `full_synthesis_prompt`. After the recent edits, the LLM returns:

```json
{
  "trade_decision": {
    "action":     "BUY|SELL|NO_TRADE",
    "setup":      "B1|B2|B3|B4|S1|S2|S3|S4|T1|T2|M1|M2|NO_TRADE",
    "entry":      <float|null>,
    "target":     <float|null>,
    "stop_loss":  <float|null>,
    "rejection":  "<string|null>",
    "next_plan":  "<string>"
  },
  "claim_registry": [
    {"id":"CR001","layer":"1W","type":"support","price":2450.0,
     "first_identified":"2024-11-04","last_tested":"2025-01-13",
     "status":"active","note":"..."}
  ],
  "full_narrative": {
    "1w_view":"...", "1d_view":"...", "4h_view":"...", "synthesis":"..."
  },
  "trend_status": {
    "1w":"UPTREND|DOWNTREND|SIDEWAYS|TRANSITIONING",
    "1d":"...", "4h":"...",
    "alignment":"ALIGNED_BULLISH|ALIGNED_BEARISH|CONFLICTED|MIXED"
  },
  "active_levels": {
    "1w_resistance":[{"price":0.0,"date_evidence":"YYYY-MM-DD","registry_id":"CRxxx"}],
    "1w_support":[...], "1d_resistance":[...], "1d_support":[...],
    "4h_resistance":[...], "4h_support":[...]
  },
  "data_integrity_check":"PASS — ..."
}
```

> `_persist()` reads `trade_decision.{action,setup,entry,target,stop_loss,rejection,next_plan}`
> and stores `raw_json=synthesis`. The engine MUST populate all of these. `claim_registry`,
> `trend_status`, `active_levels`, `full_narrative` are persisted in `raw_json` and the
> narrative table. **Acceptance test: the engine dict passes the same `_is_complete_synthesis()`
> check and round-trips through `_persist()` without code changes.**

---

## 3. Architecture — entity collaboration

```
                         ┌──────────────────────────────────────────────┐
   OHLCV (1D/1W/30m) ───▶│  FEATURE LAYER  (indicators, pure functions)  │
   + analytics.py        │  MA/EMA, ATR, swings, gap%, vol ratios,       │
   enrichments           │  VWAP, POC/VAH/VAL/HVN/LVN, Nifty RS, beta    │
                         └───────────────────┬──────────────────────────┘
                                             │ feature frames per timeframe
                         ┌───────────────────▼──────────────────────────┐
                         │  STATE EXTRACTORS  (one per timeframe)        │
                         │  trend (regression t-stat + HMM prob),        │
                         │  phase, vol character, VWAP/VP position       │
                         │     ⇒  s1w, s1d, s4h   (typed state objects)  │
                         └───────────────────┬──────────────────────────┘
                                             │
                ┌────────────────────────────┼────────────────────────────┐
                ▼                             ▼                            ▼
   ┌────────────────────┐     ┌──────────────────────────┐   ┌──────────────────────┐
   │  CLAIM REGISTRY     │     │  RECONCILE / CASCADE     │   │  SETUP SCANNERS (×12) │
   │  single chrono pass │────▶│  1W→1D→4H priority,      │◀──│  B1..B4 S1..S4 T1 T2  │
   │  S/R, gaps, VP,VWAP │     │  hard disqualifiers      │   │  M1 M2 → candidate(s) │
   │  status state machine│    └────────────┬─────────────┘   └──────────────────────┘
   └────────────────────┘                   │ candidate signals + states
                                            ▼
                            ┌───────────────────────────────┐
                            │  EVIDENCE SCORER (statistical) │
                            │  logistic reg / GBT → P(win),  │
                            │  per-signal weights (SHAP)     │
                            └───────────────┬───────────────┘
                                            │ ranked decision
                            ┌───────────────▼───────────────┐
                            │  DECISION ASSEMBLER            │
                            │  pick best, R:R gate,          │
                            │  build trade_decision          │
                            └───────────────┬───────────────┘
                                            │
                  ┌─────────────────────────┴─────────────────────────┐
                  ▼                                                    ▼
   ┌──────────────────────────┐                      ┌────────────────────────────────┐
   │  NARRATIVE GENERATOR      │                      │  JSON SERIALIZER               │
   │  template, no LLM         │                      │  exact full_synthesis schema   │
   └──────────────────────────┘                      └────────────────────────────────┘
```

**Optional overlays (off by default, for parity-debug / edge cases only):**
- *LLM narrator* — regenerate `full_narrative` prose from the structured decision (cheap).
- *LLM/ML adjudicator* — only for bars the HMM flags as ambiguous (max state prob < τ).

---

## 4. Module / file layout

```
SmartEngine/
  __init__.py
  smartengine.md                 ← this document
  engine.py                      ← public entry: run_smart_synthesis(...) -> dict
  features.py                    ← indicator/feature computation (pure functions)
  state.py                       ← TimeframeState dataclass + extractors (trend/phase/vol)
  regime.py                      ← HMM + statistical trend tests (sideways vs transitioning)
  registry.py                    ← ClaimRegistry class + single-pass updater
  setups/                        ← one module per setup family
    __init__.py
    base.py                      ← Signal dataclass, Setup ABC, shared geometry helpers
    structure.py                 ← B1-B4, S1-S4
    trend.py                     ← T1, T2
    momentum.py                  ← M1, M2
  reconcile.py                   ← cascade + hard disqualifiers
  scorer.py                      ← evidence weighting (logistic/GBT), load/predict
  narrative.py                   ← templated narrative + active_levels builder
  serialize.py                   ← assemble exact JSON schema
  labeling.py                    ← build training set from RealBacktest outcomes
  train.py                       ← fit + persist scorer/HMM models (offline)
  models/                        ← serialized model artifacts (.pkl/.json), versioned
  tests/
    test_schema_parity.py        ← engine dict passes _is_complete_synthesis + _persist
    test_setups.py               ← golden-case unit tests per setup
    test_no_lookahead.py         ← assert no future bars used
```

All thresholds come from the **existing `config.SETUP_PARAMS`** — no duplication. New
statistical hyperparameters live in a new `SMART_PARAMS` block in `config.py`.

**Shared statistical core.** `features.py`, `regime.py`, and `scorer.py` import from
`SmartAgent/quantcore.py` — a small module extracted from `allstrategy.py` holding the pure
helpers (`wilder_atr`, `momentum_tstat`, `variance_ratio`, `volume_profile`, `volume_zscore`,
`sigmoid`, `kelly_fraction`) and `HorizonLearner`. Both Hermes and SmartEngine import the
shared core; SmartEngine never imports the live trading file directly.

---

## 5. Component implementation detail

### 5.1 Feature layer (`features.py`)

Pure functions over `list[Candle]` → numpy/pandas. Reuses `analytics.py` wherever it
already exists; adds what's missing.

| Feature | Source | Notes |
|---|---|---|
| SMA20/50, EMA | new (`features.sma/ema`) | `AnchorMetrics` already has ma_20d/ma_50d |
| ATR(14) | **`allstrategy.wilder_atr`** | for volatility-normalized stops/targets |
| Momentum t-stat | **`allstrategy.momentum_tstat`** | drift quality, scale-free |
| Variance ratio | **`allstrategy.variance_ratio`** | trend vs mean-revert regime |
| Volume z-score | **`allstrategy.volume_zscore`** | participation anomaly |
| Swing highs/lows (pivots) | new (`fractal`/argrelextrema) | window = `breakout_lookback_bars` |
| gap% | `(open−prevclose)/prevclose` | identical formula to prompt |
| Volume ratio vs 20D avg | `AnchorMetrics.avg_vol_20d` | for all vol gates |
| Session/anchored VWAP, slope | **`analytics.build_vwap_block`** | already deterministic |
| POC/VAH/VAL/HVN/LVN | **`analytics.build_volume_profile_block`** | already deterministic |
| Nifty RS (1d/1w), beta, trend | **`analytics.compute_nifty_context`** | already deterministic |

> **Key asset:** the entire VWAP + Volume Profile + Nifty RS layer is *already* computed in
> Python in `analytics.py`. The LLM was merely *reading* these numbers. The engine consumes
> the same dicts — zero re-derivation, guaranteed identical to what the LLM saw.

### 5.2 Claim registry (`registry.py`) — replaces "progressive reconciliation"

The LLM chunks data and "back-propagates" because it is stateless and fallible. The engine
holds all bars and makes a **single chronological pass**, maintaining live state. No chunks,
no re-validation — labels are exact and never need correcting.

```python
@dataclass
class Claim:
    id: str; layer: str; type: str            # support|resistance|trend|gap_zone|poc|...
    price: float | None = None
    direction: str | None = None              # for trend claims
    first_identified: str = ""; last_tested: str = ""
    status: str = "active"                     # active|broken_up|broken_dn|expired
    note: str = ""
    meta: dict = field(default_factory=dict)   # gap fill target, base_low ref, tests count

class ClaimRegistry:
    def update(self, bar, layer):              # called per bar, oldest→newest
        self._age(bar)                         # >90d untested → expired
        self._detect_levels(bar, layer)        # new swings, gaps, VP/VWAP from analytics
        self._revalue(bar, layer)              # close vs each active level → status flip
        self._gap_relabel(bar)                 # BREAKAWAY filled ≤3 sessions → COMMON
        self._cascade()                        # parent invalidation → children
    def to_json(self): ...                      # → claim_registry[] schema
```

Each LLM "reconciliation" rule becomes one deterministic line:

- *support broke on close* → `if close < price*(1-tol): status="broken_dn"`
- *resistance→support role flip* → on confirmed break, flip `type`
- *breakaway gap filled in ≤3 sessions → relabel COMMON* → stored `meta["fill_target"]`,
  checked each subsequent bar within window
- *invalidation cascade* → claims hold `meta["parent"]`; invalidating a 1D trend walks its
  dependent 4H entry claims

S/R detection = pivot/fractal clustering with `sr_zone_tolerance_pct` merging; "tested ≥
`min_sr_tests` times" = count of bar lows/highs within tolerance of the level.

### 5.3 State extractors + regime model (`state.py`, `regime.py`)

Each timeframe yields a compact typed state:

```python
@dataclass
class TimeframeState:
    layer: str                       # "1W"|"1D"|"4H"
    trend: str                       # UPTREND|DOWNTREND|SIDEWAYS|TRANSITIONING
    trend_confidence: float          # 0..1  (from regression t-stat + HMM posterior)
    phase: str                       # MARKUP|MARKDOWN|ACCUMULATION|DISTRIBUTION|CONSOLIDATION
    vol_character: str               # ACCUMULATION|DISTRIBUTION|NEUTRAL|MIXED
    vwap_position: str               # ABOVE|BELOW|AT
    vp_position: str                 # INSIDE_VA|ABOVE_VAH|BELOW_VAL|AT_POC
    ma20_slope: float
    swings: list                     # recent HH/HL/LH/LL sequence
```

**Trend classification (deterministic core + statistical confirmation):**

1. **Structural rule (primary):** HH+HL ⇒ UPTREND, LH+LL ⇒ DOWNTREND, mixed ⇒ SIDEWAYS —
   identical to prompt, computed from pivots.
2. **Statistical confirmation (resolves ambiguity) — reuse Hermes primitives, don't rebuild:**
   - `allstrategy.momentum_tstat(df)` → drift quality / significance (rewards steady drift,
     punishes single spikes). Low |t| ⇒ no real trend ⇒ lean SIDEWAYS.
   - `allstrategy.variance_ratio(df, q=5)` → trending (VR>0) vs mean-reverting (VR<0) vs
     random-walk (≈0). This is the cleanest "is there structure" test and is already proven
     in production.
   - (Optional) linear-regression R² for cleanliness as a tiebreaker.
3. **"SIDEWAYS vs TRANSITIONING" (the hard one):** first try the cheap rule —
   `TRANSITIONING` when `variance_ratio` is crossing zero / `momentum_tstat` is rising from
   insignificance while structure is still mixed. Only if this proves unreliable in the
   backtest, add a **Gaussian HMM** (`hmmlearn`) on
   `[return, |return|, volume_z]` with K=4 hidden states. Output is a posterior
   distribution per bar. Decision rule:
   - max posterior ≥ τ_high (e.g. 0.6) → that state is the label.
   - state probability mass *moving* between range↔trend over last N bars → **TRANSITIONING**.
   - `trend_confidence = max posterior`. Bars with max posterior < τ_low are flagged
     `ambiguous=True` (only these may invoke the optional adjudicator).

This makes TRANSITIONING a *measured* state (probability flux), not an eyeball call —
directly improving on the LLM, whose TRANSITIONING was inconsistent.

### 5.4 Setup scanners (`setups/`)

One pure function per setup, signature:

```python
def check_B2(s1w, s1d, s4h, registry, feats, params) -> Signal | None
```

Returns a `Signal(name, direction, entry, stop, target, evidence: dict)` or `None`. Each
function is a **direct transcription** of the taxonomy checklist in `prompts.py`. Example
(B2 · BREAKOUT_RETEST), abbreviated:

```python
def check_B2(s1w, s1d, s4h, reg, f, p):
    if s1w.trend == "DOWNTREND": return None                      # 1W non-bearish
    bo = reg.recent_breakout(layer="1D",
            within=(p["trap_lookback_bars"], p["breakout_lookback_bars"]),
            vol_min=p["breakout_vol_min"])
    if not bo: return None
    if reg.closed_below_since(bo.level, bo.date): return None      # no 1D close back below
    if not s4h.held_at(bo.level): return None                     # retest holds
    entry = bo.level
    stop  = f.pullback_low or reg.nearest_support_below(entry)
    target = reg.next_resistance_above(bo.high) \
             or measured_move_B2(bo, reg, p)                       # CRsyn_B2 fallback
    if rr(entry, stop, target) < p["min_rr_ratio"]: return None
    return Signal("B2","BUY",entry,stop,target, evidence=collect_evidence(...))
```

Shared helpers in `base.py`: `rr()`, measured-move formulas (period-high/low exceptions →
register `CRsyn_*` SYNTHETIC claims, identical math to prompt), close-in-range %, wick ratio.
The **M1/T2 TRANSITIONING exception** (added earlier) is encoded as:
`s1w.trend in ("UPTREND",) or (s1w.trend=="TRANSITIONING" and s1d.trend==s4h.trend=="UPTREND")`.

### 5.5 Reconcile + hard disqualifiers (`reconcile.py`)

```python
def reconcile(s1w, s1d, s4h, feats, params):
    # hard disqualifiers first → NO_TRADE
    if conflict(s1w.trend, s1d.trend): return Reject("timeframe_conflict")
    if within_pct(feats.last_close, feats.poc, params["poc_dead_zone_pct"]):
        return Reject("poc_dead_zone")
    if circuit_event(feats): return Reject("circuit")
    if block_deal(feats, params): return Reject("block_deal")
    # permitted direction(s) from cascade
    return permitted_directions(s1w, s1d)     # 1W governs; SIDEWAYS → range extremes
```

The decision assembler then iterates setups in priority order
`B1→B2→B3→B4→T1→T2→M1→M2` (or SELL mirror) — exactly the HUNT PROTOCOL order — and
collects candidates.

### 5.6 Evidence scorer (`scorer.py`) — the statistical heart

The scorer is **pluggable behind a stable interface** so SmartEngine never blocks on Hermes's
in-progress learner. Three interchangeable implementations, swapped via
`SMART_PARAMS["scorer"]`:

```python
class Scorer(Protocol):
    def predict(self, signal, states, feats) -> float: ...   # P(win)/confidence 0..1
    def learn(self, signal, outcome) -> None: ...            # no-op if not learning
```

**(A) `RuleScorer` — default, ships first, zero learning.** Confidence = a transparent
weighted count of confirmed evidence (each setup's own ✓ gates), normalized to 0..1. No
training data, no Hermes, no ML. This alone lets SmartEngine fully replace the LLM in Phases
0–3: the rules already pick the trade; this just provides a confidence/ranking number and a
gate (`≥ smart_min_prob`). **This is the critical-path scorer.**

**(B) `LogisticScorer` — SmartEngine's own learned scorer, self-contained.** Offline logistic
regression fit on RealBacktest outcome labels (§7). Calibrated P(win); coefficients = evidence
weights. **Owned entirely by SmartEngine — no dependency on Hermes.** This is the upgrade once
labels exist, and the safe choice while Hermes learning matures.

**(C) `HermesLearnerScorer` — optional, adopt only after Hermes learning is validated.** Wraps
a single `HorizonLearner` instance (swing/daily config) fed the post-cascade evidence vector.
Strictly more capable (online EG, dual-book MoE, Platt calib) — but **gated behind Hermes
readiness**, not required for ship.

```python
# scorer chosen by config; engine code is identical regardless
scorer = make_scorer(SMART_PARAMS["scorer"])   # "rule" | "logistic" | "hermes"
```

Decision rule (identical for all three): among confirmed candidates, pick `argmax
score` subject to `score ≥ smart_min_prob` AND `R:R ≥ min_rr_ratio`; else NO_TRADE.

**Migration path:** ship with `rule` → switch to `logistic` once backtest labels are
collected → switch to `hermes` if/when its learning is proven. Each is a one-line config
change; the engine, schema, and toggle are unaffected.

### 5.7 Narrative generator (`narrative.py`) — no LLM

Templated, deterministic prose assembled from the structured states + chosen setup. Example:

```
1d_view: "1D {trend} (conf {tc:.0%}); price {vp_position} vs 20D POC ({poc}); volume
          {vol_character}. Key support {s} (CR{n}), resistance {r} (CR{m})."
```

Produces all four `full_narrative` keys at the same word budgets the schema declares. Builds
`active_levels` directly from the registry (active S/R per layer with `registry_id`). Optional
LLM-narrator overlay can replace these strings if richer prose is desired — but it is OFF by
default and never affects the decision.

### 5.8 Serializer (`serialize.py`)

Assembles the final dict in the exact schema of §2. Sets:
- `trade_decision.next_plan` — templated ("Watch CRxxx (₹..) retest; if close ≥ .. on vol ≥
  .. re-evaluate M1") so `_persist` is fully satisfied.
- `trade_decision.rejection` — the disqualifier/step that produced NO_TRADE, else null.
- `data_integrity_check` — `"PASS — deterministic engine; all prices sourced from OHLCV;
  every level has a claim_registry id"` (every cited price is a registry entry by
  construction → integrity is guaranteed, not asserted).

---

## 6. The toggle (seamless LLM ⇄ SmartEngine)

**config.py**

```python
DECISION_ENGINE = os.environ.get("PA_DECISION_ENGINE", "llm")   # "llm" | "smart"
SMART_PARAMS = { "smart_min_prob": 0.55, "hmm_states": 4,
                 "regime_tau_high": 0.60, "regime_tau_low": 0.40,
                 "scorer": "logistic", "model_dir": str(BASE_DIR.parent/"SmartEngine"/"models") }
```

**synthesis.py — single switch point** in `run_full_synthesis` (and the batch builder):

```python
from config import DECISION_ENGINE
...
if DECISION_ENGINE == "smart":
    from SmartEngine.engine import run_smart_synthesis
    return run_smart_synthesis(symbol, window_start, window_end, next_td,
                               weekly, daily, h4, anchor_block, anchor_metrics_dict)
# else existing LLM path (call_claude → _parse_json) unchanged
```

`run_smart_synthesis` returns the same dict shape `_parse_json` returns, so `_persist`,
DB schema, and all downstream code are untouched. Batch mode simply skips the API entirely
when `smart` (no batch submission needed — it's local and instant).

**No schema migration. No consumer changes. Flip one env var.**

---

## 7. Self-learning loop (SniperAgent mandate)

```
RealBacktest ──▶ labeling.py ──▶ training set (features + win/loss/timeout)
                                        │
                              train.py: fit HMM + scorer
                                        │
                                models/ (versioned artifacts)
                                        │
              engine loads latest model at runtime ──▶ decisions
                                        │
              new realized trades ──▶ append labels ──▶ periodic retrain
```

- **`labeling.py`:** replay deterministic setups over history; for each fired signal, record
  forward outcome (hit target before stop = win; hit stop = loss; timed out = neutral) and
  the evidence vector. This is the supervised dataset.
- **Stage 1 — no learning (ships first):** `RuleScorer`. Deterministic confidence from the
  setups' own gates. Replaces the LLM immediately; learning is not required to go live.
- **Stage 2 — SmartEngine's own offline learner (`train.py` → `LogisticScorer`):** fit a
  calibrated logistic regression on the labels; **walk-forward** split to avoid curve-fit;
  persist versioned artifact. Self-contained — does **not** depend on Hermes.
- **Stage 3 — Hermes online learner (optional, gated on Hermes readiness):** once Hermes's
  learning is validated, swap to `HermesLearnerScorer`, feeding each resolved outcome to
  `HorizonLearner.learn(trade)` (online EG + dual-book + Platt). Adopt only when proven.
- **Threshold optimization:** grid/Bayesian search over `SETUP_PARAMS` (e.g.
  `breakout_lookback_bars`, `momentum_vol_min`, `smart_min_prob`) maximizing out-of-sample
  expectancy/Sharpe. Connects to the earlier finding that these params should be *fit*, not
  guessed.
- **Drift guard:** keep the LLM path as an oracle; periodically diff (see §8) and alert if
  the engine and a sampled LLM run diverge beyond tolerance.

---

## 8. Validation plan — proving "equal or better"

1. **Schema parity (blocking):** `test_schema_parity.py` — engine dict passes
   `_is_complete_synthesis()` and a dry `_persist()` against a temp DB. CI gate.
2. **Decision agreement:** run engine + LLM on the same N symbols × M historical dates;
   confusion matrix of `action` and `setup`. Investigate every disagreement — each is either
   an LLM error, an engine gap, or a genuinely ambiguous bar.
3. **Outcome backtest (the real test):** trade both decision streams through RealBacktest over
   the same period; compare **win-rate, expectancy, profit factor, max drawdown, # trades**.
   Acceptance: SmartEngine ≥ LLM on expectancy and profit factor at equal-or-higher trade
   count. (We expect *better*, because the engine never miscomputes a level, never skips a
   setup, and applies *learned* weights instead of vibes.)
4. **Latency/cost:** assert engine < 1s/symbol and $0 marginal — vs ~5 min and ~$0.10–0.33.
5. **Ablation:** with scorer disabled (pure rules) vs enabled, to quantify the statistical
   layer's contribution.

---

## 9. Phased roadmap

| Phase | Deliverable | Exit criterion |
|---|---|---|
| 0 | Skeleton + `serialize.py` + toggle; engine returns NO_TRADE with valid schema | parity test green; toggle works end-to-end |
| 1 | `features.py` + `registry.py` (1D only) + `state.py` deterministic trend | registry evolves S/R correctly vs hand-checked chart |
| 2 | All 12 setups (`setups/`) + `reconcile.py`, rules-only (no scorer) | decision agreement vs LLM ≥ 70% on a sample |
| 3 | Multi-timeframe (1W/4H) + cascade + `narrative.py` + `active_levels` | full schema populated; agreement ≥ 80% |
| 4 | `regime.py` (Hermes `variance_ratio`/`momentum_tstat`; HMM only if needed) | TRANSITIONING/SIDEWAYS resolved with confidence |
| 5 | `labeling.py` + `train.py` + `LogisticScorer` (SmartEngine-owned) | outcome backtest ≥ LLM expectancy |
| 6 | *Optional, gated on Hermes:* `HermesLearnerScorer`; param optimization; drift guard | online self-tuning loop live |

**Critical path = Phases 0–3 with `RuleScorer`** — a rules-only engine that fully replaces the
LLM at zero cost, with **no dependency on Hermes's learning**. Phase 5 adds SmartEngine's own
learned scorer (still independent of Hermes). Phase 6 (Hermes online learner) is **optional and
gated on Hermes readiness** — it is never a blocker.

---

## 10. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Edge-case patterns the rules miss | Optional LLM/ML adjudicator on HMM-flagged ambiguous bars only (rare, cheap) |
| Overfitting the scorer to history | Reuse Hermes `HorizonLearner` (online EG + Platt calib, low η on slow horizons = built-in overfit protection); walk-forward bootstrap |
| Coupling SmartEngine to live trading file | Extract shared `quantcore.py`; SmartEngine imports the core, never `allstrategy.py` directly |
| Duplicating Hermes's statistical work | Reuse its **stable feature math** now; learner only later (§0) |
| **Hermes learning still in progress** | **Scorer is pluggable (§5.6): ship on `RuleScorer` (no learning), upgrade to SmartEngine's own `LogisticScorer`; adopt Hermes `HorizonLearner` only once proven. Hermes is never on the critical path.** |
| Pivot/S-R detection differs from LLM's | Tolerance-merge + golden unit tests; LLM as oracle in diff harness |
| Schema drift if prompt changes later | Single `serialize.py` owns the schema; parity test in CI catches drift |
| Thin/illiquid symbols, corporate actions | Port the NSE-mechanics disqualifiers (circuit, block-deal, ex-div) into `reconcile.py` verbatim |
| Loss of human-readable rationale | Templated narrative + per-signal contributions = more auditable than prose |

---

## 11. Why this can beat the LLM

- **No arithmetic/labeling errors** — `close < support` is exact every time; the LLM
  occasionally miscites levels or skips a setup.
- **Full taxonomy coverage** — all 12 setups always evaluated in order; no context-pressure
  truncation, no drift.
- **Learned evidence weights** — the conflict-weighting that the LLM does "by vibe" becomes a
  calibrated, backtested function that improves as data accumulates.
- **Reproducible & auditable** — identical inputs → identical outputs, with per-signal
  attribution; essential for a trading system and for the self-improvement loop.
- **Free and instant** — enables scanning the full universe many times per day and large-scale
  parameter search that is economically impossible with the LLM.

The LLM remains valuable as (a) a **reference oracle** during validation and (b) an **optional
narrator / edge-case adjudicator** — but it exits the hot path. The decision becomes
deterministic, calibrated, and self-improving.
