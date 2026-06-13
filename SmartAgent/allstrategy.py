#!/usr/bin/env python3
"""
==============================================================================
 HERMES OMNIHORIZON — Multi-Horizon Bayesian Confluence Engine (NSE)
==============================================================================

Evolution of strategy.py (HERMES CONFLUX, intraday-only) into a single
engine that trades FOUR horizons simultaneously, each with its OWN
continuously-adapting brain:

  ┌────────────┬───────────┬─────────────┬──────────────┬───────────────────┐
  │ Horizon    │ Bars      │ Hold        │ Barriers     │ Learner           │
  ├────────────┼───────────┼─────────────┼──────────────┼───────────────────┤
  │ intraday   │ 1-minute  │ ≤45 min,    │ 2.5/1.25 ATR │ own weights, p*,  │
  │            │           │ sq-off 15:15│              │ eta=0.10          │
  │ short_term │ 30-minute │ ≤7 days     │ 3.0/1.50 ATR │ own weights, p*,  │
  │            │           │             │              │ eta=0.08          │
  │ swing      │ daily     │ ≤30 days    │ 4.0/2.00 ATR │ own weights, p*,  │
  │            │           │             │              │ eta=0.06          │
  │ positional │ weekly    │ ≤120 days   │ 6.0/3.00 ATR │ own weights, p*,  │
  │            │           │             │              │ eta=0.05          │
  └────────────┴───────────┴─────────────┴──────────────┴───────────────────┘

The same physics, four resolutions
----------------------------------
Markets are (approximately) self-similar: auction structure, participation
anomalies and flow imbalances carry signal at every timescale, but with
DIFFERENT relative importance. OMNIHORIZON therefore runs the identical
evidence->fusion->Kelly->triple-barrier pipeline on each horizon's native
bars, and lets each horizon LEARN ITS OWN weighting of the evidence.
Nothing is hand-tuned to a timescale beyond the priors: if order-book flow
is useless for positional trades, the positional learner will discover that
and shrink its flow weight — independently of the intraday learner, which
may amplify it.

Evidence vector (8 features, each squashed phi_i = tanh(x_i / 2))
-----------------------------------------------------------------
  x1 structure : (C - POC) / ATR        auction breakout strength
  x2 value     : (C - VAH) / ATR        acceptance above value area
  x3 vol_anom  : z-score(volume, 20)    participation anomaly
  x4 mom_t     : t-statistic of log returns over K bars
                 t = mean(r) / (std(r)/sqrt(K))  — momentum QUALITY, not
                 magnitude: rewards steady drift, punishes one noisy spike
  x5 vratio    : ln VR(q),  VR(q) = Var(r_q) / (q Var(r_1))   (Lo-MacKinlay)
                 > 0 trending regime, < 0 mean-reverting regime — tells each
                 horizon whether breakout evidence should be trusted
  x6 flow      : ln(bidQ / askQ) on the ATM CE   order-book imbalance
  x7 pcr       : -ln(PCR_strike)          put/call OI tilt at the ATM strike
  x8 vrp       : (RV_h - IV) / IV         variance risk premium, where RV_h
                 is EWMA realised vol measured ON THE HORIZON'S OWN BARS and
                 annualised with that bar's frequency

Fusion with cross-horizon coupling (the wow)
--------------------------------------------
Per horizon h:    L_h = b0 + SUM_i w_h,i * phi_h,i          (raw log-odds)
Coupled:          L'_h = L_h + KAPPA * tanh( mean_{g!=h} L_g / 2 )
                  p_h = sigma(L'_h)
A hierarchical agreement bonus: when independent resolutions of the market
agree, each horizon's posterior is nudged up (or down) — bounded by KAPPA,
so coupling can never overturn strong local evidence, only break ties.
Cross-horizon log-odds are cached from each horizon's latest evaluation
(slow horizons move slowly, so staleness is bounded and harmless).

Per-horizon continuous learning
-------------------------------
Each horizon owns an exponentiated-gradient (Hedge) learner, identical in
form to CONFLUX but fully separate in state:
    w_h,i <- clip( w_h,i * exp(eta_h * R * phi_i_entry), w_min, w_max )
    renormalise so SUM_i w_h,i = W0_h            (evidence-mass conservation)
    p*_h  <- +0.015 after a loss / -0.005 after a win, bounded [0.55, 0.80]
eta_h decreases with horizon length: slow horizons see few, noisy outcomes,
so they take smaller steps (overfitting protection).

Sizing, exits, capital
----------------------
Fractional Kelly per horizon with payoff ratio beta_h = R_T,h / R_S,h.
Capital is split into sleeves (intraday 40%, short 25%, swing 20%,
positional 15%); each trade risks at most 5% of its sleeve.
Triple-barrier exits per horizon: PT/SL in horizon-ATR units, chandelier
trail, and a time barrier in the horizon's natural units. Intraday is
hard square-off at 15:15 IST (no overnight gap risk in that sleeve).

Crash-safety (new vs strategy.py)
---------------------------------
Swing/positional trades outlive the process, so open positions are
persisted to SQLite on every mutation and re-hydrated on restart.
All learning state is keyed by horizon in the DB.

Upstox endpoints (official docs)
--------------------------------
  intraday bars : GET /v3/historical-candle/intraday/{key}/{unit}/{interval}
  history bars  : GET /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}
  expiries      : GET /v2/option/contract?instrument_key=...
  option chain  : GET /v2/option/chain?instrument_key=...&expiry_date=...
==============================================================================
"""

import os
import json
import time
import sqlite3
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from upstox_ratelimit import http_get   # quota guard: 45/s, 450/min, 1900/30m

IST = ZoneInfo("Asia/Kolkata")   # all session logic is IST, host TZ-agnostic

# ==============================================================================
# CONFIGURATION
# ==============================================================================
# Prefer env var; falls back to placeholder (tokens expire daily — do not
# hardcode them into files you might share or commit).
ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN", "YOUR_UPSTOX_ACCESS_TOKEN")
BASE_URL = "https://api.upstox.com"
# Brain state DB — stored in shared Data/SmartAgent/ so no runtime files land in source.
# Override: OMNIHORIZON_DB=/path/to.db
_AGENT_DATA_DIR = Path(__file__).resolve().parent.parent / "Data" / "SmartAgent"
_AGENT_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = os.environ.get("OMNIHORIZON_DB",
                          str(_AGENT_DATA_DIR / "hermes_omnihorizon_v2.db"))

UNIVERSE = {
    "NSE_EQ|INE002A01018": {"symbol": "RELIANCE",  "iv_cap": 75, "has_options": True},
    "NSE_EQ|INE040A01034": {"symbol": "HDFCBANK",  "iv_cap": 70, "has_options": True},
    "NSE_EQ|INE090A01021": {"symbol": "ICICIBANK", "iv_cap": 70, "has_options": True},
    "NSE_EQ|INE467B01029": {"symbol": "TCS",       "iv_cap": 70, "has_options": False},
    "NSE_EQ|INE062A01020": {"symbol": "SBIN",      "iv_cap": 70, "has_options": False},
}

# --- NSE-100 expansion -----------------------------------------------------------
# Generate universe_nse100.py once with build_universe.py (resolves official
# instrument keys from Upstox's instruments file — never hand-type ISINs).
# If present, it replaces the default 5-symbol universe above. Backtests are
# unaffected (they define their own universe).
try:
    from universe_nse100 import UNIVERSE as _UNIVERSE_100
    UNIVERSE = _UNIVERSE_100
    print(f"[UNIVERSE] universe_nse100.py loaded: {len(UNIVERSE)} symbols "
          f"({sum(1 for v in UNIVERSE.values() if v['has_options'])} "
          f"options-fed)")
except ImportError:
    pass

FEATURES = ["structure", "value", "vol_anom", "mom_t", "vratio",
            "flow", "pcr", "vrp", "maturity", "accel",
            "align", "mom_l", "mom_v"]

# --- HERMES V2 abilities (battle-test finding 2026-06-12: entry starvation,
# conviction deadlock, scrap-harvesting trail). Master kill-switch OMNI_V2=0
# restores v1 behaviour; harnesses may toggle individual flags for A/B. ---
_V2_ON = os.environ.get("OMNI_V2", "1") == "1"
V2 = {
    "shadow": _V2_ON,        # counterfactual learning from below-p* evals
    "regime_gate": _V2_ON,   # trend/chop dual weight books gated by VR
    "pstar_decay": _V2_ON,   # idle decay of p* back toward p_base
    "calib_warmup": _V2_ON,  # Platt identity until enough samples + a-floor
    "trail_late": _V2_ON,    # arm the trail as a fraction of PT distance
    "new_features": _V2_ON,  # x9 maturity (range position), x11 accel
    "trend_pack": _V2_ON,    # x12 HTF alignment, x14 long momentum,
                             # x16 volume-confirmed momentum, and the
                             # regime-conditioned maturity ("trend is your
                             # friend" + momentum-spectrum upgrade)
    "align_veto": _V2_ON,    # hard gate: refuse longs against a decisively
                             # falling higher-timeframe trend. EARNED by the
                             # v2_trend battery: 10/10 misaligned entries
                             # lost (-0.482R mean = the book's entire net
                             # loss). Vetoed entries become shadows, so the
                             # rule is audited forever and stays falsifiable.
    "mass_renorm": _V2_ON,   # scale the 13-feature weight budget back to
                             # the original 8-feature evidence mass, so L's
                             # scale matches v1 and Platt's a is not pinned
                             # at its floor (v2_trend: a=0.50 on all
                             # horizons = binding).
}

# --- horizon enable switches ---------------------------------------------------
# Only enabled horizons take NEW entries. Open positions of a disabled horizon
# are still reconciled and exited normally (never orphan a live trade), and its
# learner still learns from those exits.
HORIZON_ENABLED = {
    "intraday":   True,
    "short_term": True,
    "swing":      True,
    "positional": True,
}

# --- per-horizon profiles -----------------------------------------------------
# ann_factor: bars per year for realised-vol annualisation
# max_hold:   ("min", n) or ("days", n) — time barrier in natural units
HORIZONS = {
    "intraday": {
        "unit": "minutes", "interval": "1",  "lookback_days": 0,
        "min_bars": 30, "atr_period": 14,
        "rt": 2.5, "rs": 1.25, "arm_atr": 1.0, "trail_atr": 1.0,
        "max_hold": ("min", 45), "eval_every_min": 1,
        "max_extensions": 3,
        "sleeve": 0.40, "eta": 0.10, "p_base": 0.70,
        "ann_factor": 375 * 252, "square_off": "15:15",
        "w_init": {"structure": 1.1, "value": 0.7, "vol_anom": 0.8,
                   "mom_t": 0.6, "vratio": 0.5, "flow": 1.1,
                   "pcr": 0.8, "vrp": 0.6},
    },
    "short_term": {
        "unit": "minutes", "interval": "30", "lookback_days": 20,
        "min_bars": 30, "atr_period": 14,
        "rt": 3.0, "rs": 1.5, "arm_atr": 1.0, "trail_atr": 1.25,
        "max_hold": ("days", 7), "eval_every_min": 30,
        "max_extensions": 2,
        "sleeve": 0.25, "eta": 0.08, "p_base": 0.70,
        "ann_factor": int(375 / 30 * 252), "square_off": None,
        "min_profit_pct": 0.02,   # NSE cash: skip if 3×ATR PT < 2% from entry
        "w_init": {"structure": 1.0, "value": 0.8, "vol_anom": 0.7,
                   "mom_t": 0.9, "vratio": 0.7, "flow": 0.8,
                   "pcr": 0.8, "vrp": 0.7},
    },
    "swing": {
        "unit": "days", "interval": "1", "lookback_days": 300,
        "min_bars": 60, "atr_period": 14,
        "rt": 4.0, "rs": 2.0, "arm_atr": 1.5, "trail_atr": 1.5,
        "max_hold": ("days", 30), "eval_every_min": 24 * 60,
        "max_extensions": 2,
        "sleeve": 0.20, "eta": 0.06, "p_base": 0.70,
        "ann_factor": 252, "square_off": None,
        "min_profit_pct": 0.03,   # skip if 4×ATR PT < 3% (target band 3–4%)
        "w_init": {"structure": 0.9, "value": 0.8, "vol_anom": 0.6,
                   "mom_t": 1.2, "vratio": 1.0, "flow": 0.5,
                   "pcr": 0.7, "vrp": 0.7},
    },
    "positional": {
        "unit": "weeks", "interval": "1", "lookback_days": 750,
        "min_bars": 50, "atr_period": 10,
        "rt": 6.0, "rs": 3.0, "arm_atr": 2.0, "trail_atr": 2.0,
        "max_hold": ("days", 120), "eval_every_min": 24 * 60,
        "max_extensions": 1,
        "sleeve": 0.15, "eta": 0.05, "p_base": 0.70,
        "ann_factor": 52, "square_off": None,
        "min_profit_pct": 0.05,   # skip if 6×ATR PT < 5% from entry
        "w_init": {"structure": 0.8, "value": 0.7, "vol_anom": 0.5,
                   "mom_t": 1.4, "vratio": 1.2, "flow": 0.3,
                   "pcr": 0.5, "vrp": 0.6},
    },
}

# --- shared model hyper-parameters --------------------------------------------
VOL_Z_WINDOW   = 20
EWMA_LAMBDA    = 0.94
PROFILE_BINS   = 24
VALUE_AREA_PCT = 0.70
MOM_WINDOW     = 20       # bars for the momentum t-statistic
VR_Q           = 5        # variance-ratio aggregation period
P_STAR_MIN, P_STAR_MAX = 0.55, 0.80
P_STAR_NO_OPTIONS = 0.65   # FLOOR on entry bar when flow/pcr plane unavailable:
                           # effective p* = max(learner.p_star, this) — never
                           # easier than the adaptive bar (see entry_p_star)
W_MIN, W_MAX   = 0.10, 2.50
BIAS0          = -1.0     # prior log-odds (scepticism)
KAPPA          = 0.30     # cross-horizon coupling strength (bounded bonus)

# --- PCR feature mode (x7) ------------------------------------------------------
# "level": x7 = -ln(PCR) as a level (legacy). OI accumulates, so the level is a
#   slowly saturating integral of past flow — near-constant at entry time, zero
#   discrimination (battery finding, 2026-06-12: pcr renorm-diluted everywhere).
# "delta": x7 = change in -ln(PCR) over the trailing PCR_DELTA_WINDOW_S
#   seconds, SELF-NORMALIZED by an EWMA of its own absolute size — fresh
#   positioning, where the signal lives. Normalization makes the feature
#   scale-free (like the z-score/t-stat/ATR-relative features) and immune to
#   the 1/OI shrink of ln-deltas as absolute OI grows.
# A/B via `backtest.py --pcr-mode level|delta` or env OMNI_PCR_MODE.
PCR_MODE = os.environ.get("OMNI_PCR_MODE", "delta")
PCR_DELTA_WINDOW_S = 1800  # baseline lookback for the delta (30 min)
PCR_MAD_LAMBDA     = 0.97  # EWMA decay for the mean-absolute-delta normalizer

# --- gate telemetry -------------------------------------------------------------
# Counts every evaluate_entry outcome per horizon: "{horizon}.{gate}".
# Gates: evaluated, entered, already_open, min_bars, p_star, iv_cap, cooldown,
# min_profit, cost_gate, kelly_zero. Read/cleared by the backtest harness;
# answers "why didn't it trade" without guessing.
GATE_STATS = {}


def _gate(horizon, name):
    k = f"{horizon}.{name}"
    GATE_STATS[k] = GATE_STATS.get(k, 0) + 1

# --- re-underwriting time stop -------------------------------------------------
# The vertical barrier is conditional, not a dumb clock. When a position
# reaches its max_hold, the engine re-scores the thesis with CURRENT evidence:
#   p_now >= P_HOLD  -> extend one more hold window (up to max_extensions)
#   p_now <  P_HOLD  -> the edge has decayed; exit TIME_EXIT
# During gap replay (or if fresh data is unavailable) it falls back to a
# failure-to-perform check: extend only if the trade ever reached
# PROGRESS_MIN_R of its risk. Intraday's 15:15 square-off remains a HARD cap,
# so labels stay bounded regardless of extensions.
P_HOLD          = 0.55    # thesis-still-alive threshold at re-underwriting
PROGRESS_MIN_R  = 0.5     # fallback: min progress (in R) to earn an extension
LIVE_EDGE_SLACK = 180     # bar within this many seconds of now = live edge
CAPITAL        = 1_000_000
KELLY_FRACTION = 0.25     # quarter-Kelly
RISK_CAP       = 0.05     # max risk per trade as fraction of the SLEEVE
R_CLIP_LO, R_CLIP_HI = -2.0, 3.0

# --- cost-aware entry gate ------------------------------------------------------
# Detecting edge is not the same as monetising it: when the stop distance is
# of the same order as round-trip costs, every trade pays ~1R to the broker
# before the market moves (the classic 1-minute-bar trap, exposed by the
# backtest battery). Gate every entry on NET expected R:
#     E[R_gross] = p*beta - (1-p)                   barrier-geometry expectation
#     cost_R     = 2 * COST_BPS/1e4 * entry / stop_dist     round trip, in R
#     enter only if  E[R_gross] - cost_R >= MIN_NET_EDGE_R
# The gate self-regulates with volatility: small ATR -> huge cost_R -> blocked;
# vol expansion -> cost_R shrinks -> trading re-enabled.
COST_BPS_ROUNDTRIP = 10.0   # brokerage + STT + slippage, round trip
MIN_NET_EDGE_R     = 0.20   # required net expected R after costs

# --- post-exit cooldown ---------------------------------------------------------
# After any exit, no re-entry in the same (horizon, symbol) for the horizon's
# cooldown — prevents churn (immediate re-entry into the same decaying setup,
# paying costs each time). Expressed in minutes per horizon.
COOLDOWN_MIN = {"intraday": 10, "short_term": 120,
                "swing": 1440, "positional": 7200}

# --- online probability calibration (Platt scaling) ------------------------------
# The fused posterior p = sigma(L) is only as honest as its calibration: if the
# model says 0.65 but wins 30% of the time, the Kelly stake and the cost gate's
# E[R] arithmetic are built on a lie. Each horizon therefore learns a Platt map
#     p_cal = sigma(a * L + b)            (a=1, b=0 at cold start = identity)
# updated online by SGD on the log-loss of each closed trade (y = 1 if R > 0):
#     a <- a - lr * (p_cal - y) * L ,  b <- b - lr * (p_cal - y)
# Overconfidence shrinks a below 1 (compressing p toward 0.5) and pushes b
# negative; a well-calibrated model keeps a~1, b~0. p_cal feeds the p* gate,
# Kelly sizing, the cost gate, and re-underwriting — raw p is logged alongside.
CALIB_LR = 0.05
CALIB_A_MIN, CALIB_A_MAX = 0.2, 3.0
CALIB_B_MIN, CALIB_B_MAX = -2.0, 2.0

# --- V2 constants -----------------------------------------------------------------
CALIB_MIN_N = 15          # Platt stays identity until this many outcomes
CALIB_A_FLOOR = 0.5       # calibration may deflate, never crush, conviction
PSTAR_DECAY_PER_DAY = 0.003   # idle release of the loss-ratchet (toward p_base)
TRAIL_ARM_PT_FRAC = 0.55  # arm trail at >= this fraction of the PT distance
REGIME_GATE_K = 2.0       # g = sigma(K * smoothed VR): trend vs chop blend
REGIME_EWMA = 0.85        # smoothing of x5 for the gate
SHADOW_ETA_SCALE = 0.25   # shadow trades teach at a quarter of real authority
SHADOW_PSTAR_WIN = 0.004  # winning shadow lowers the bar (recovery channel)
SHADOW_PSTAR_LOSS = 0.001 # losing shadow gently confirms it
SHADOW_REG_GAP_EVALS = 4  # min evaluation slots between new shadows per key
MATURITY_WINDOW = 200     # bars for the x9 range-position anchor
ACCEL_LAG = 5             # bars for the x11 momentum-acceleration delta

# --- trend/momentum pack constants ------------------------------------------------
# x12 alignment: each horizon hears the NEXT-SLOWER horizon's trend (its
# momentum t-stat on native bars). Positional anchors on its own secular
# drift (double-window t-stat). Cached per symbol at the source's cadence
# so live API cost is negligible.
ALIGN_SOURCE = {"intraday": "short_term", "short_term": "swing",
                "swing": "positional", "positional": None}
ALIGN_TTL_S = {"short_term": 1800, "swing": 14400, "positional": 86400}
SECULAR_WINDOW = 2 * MOM_WINDOW   # positional's own-drift window (40 bars)
MOM_L_MULT = 5            # x14 long-momentum window = 5x MOM_WINDOW
PULLBACK_WINDOW = 40      # x9 trend-mode: swing-high lookback
PULLBACK_ATR_SCALE = 0.5  # x9 trend-mode: penalty per ATR below the high
MISALIGN_PHI = -0.20      # telemetry: entry counted misaligned below this
# priors for the new features (others default 0.6); alignment and the
# momentum spectrum start with a real voice — they exist to be heard
V2_FEATURE_PRIORS = {"maturity": 0.6, "accel": 0.6,
                     "align": 0.8, "mom_l": 0.8, "mom_v": 0.6}


# ==============================================================================
# PERSISTENCE LAYER (all state keyed by horizon)
# ==============================================================================
def init_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            horizon TEXT, symbol TEXT,
            entry_time TEXT, exit_time TEXT,
            entry_price REAL, target REAL, initial_sl REAL, exit_price REAL,
            qty REAL, outcome TEXT, pnl REAL, r_multiple REAL,
            p_entry REAL, kelly_f REAL, atr REAL, features_json TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS brain_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            horizon TEXT, timestamp TEXT, weights_json TEXT, p_star REAL,
            total_trades INTEGER, wins INTEGER,
            calib_a REAL DEFAULT 1.0, calib_b REAL DEFAULT 0.0
        )
    """)
    # migration for DBs created before the calibration layer
    for col, default in (("calib_a", 1.0), ("calib_b", 0.0)):
        try:
            c.execute(f"ALTER TABLE brain_state ADD COLUMN {col} REAL "
                      f"DEFAULT {default}")
        except sqlite3.OperationalError:
            pass    # column already exists
    # crash-safe open positions: swing/positional must survive restarts
    c.execute("""
        CREATE TABLE IF NOT EXISTS open_positions (
            horizon TEXT, symbol TEXT, payload_json TEXT,
            PRIMARY KEY (horizon, symbol)
        )
    """)
    conn.commit()
    conn.close()


def db_log_trade(t):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO trades (horizon, symbol, entry_time, exit_time,
                            entry_price, target, initial_sl, exit_price, qty,
                            outcome, pnl, r_multiple, p_entry, kelly_f, atr,
                            features_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (t["horizon"], t["symbol"], t["entry_time"], t["exit_time"],
          t["entry_price"], t["target"], t["initial_sl"], t["exit_price"],
          t["qty"], t["outcome"], t["pnl"], t["r_multiple"], t["p_entry"],
          t["kelly_f"], t["atr"], json.dumps(t["phi"])))
    conn.commit()
    conn.close()


def db_save_position(t):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "REPLACE INTO open_positions (horizon, symbol, payload_json) "
        "VALUES (?,?,?)",
        (t["horizon"], t["symbol"], json.dumps(t)))
    conn.commit()
    conn.close()


def db_delete_position(horizon, symbol):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM open_positions WHERE horizon=? AND symbol=?",
                 (horizon, symbol))
    conn.commit()
    conn.close()


def db_load_positions():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT horizon, symbol, payload_json FROM open_positions").fetchall()
    conn.close()
    return {(h, s): json.loads(p) for h, s, p in rows}


# ==============================================================================
# UPSTOX DATA PIPELINE
# ==============================================================================
def _headers():
    return {"Accept": "application/json",
            "Authorization": f"Bearer {ACCESS_TOKEN}"}


def _candles_to_df(candles):
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high",
                                        "low", "close", "volume", "oi"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.iloc[::-1].reset_index(drop=True)   # API is newest-first


def fetch_intraday_bars(instrument_key, unit="minutes", interval="1"):
    """GET /v3/historical-candle/intraday/{key}/{unit}/{interval}"""
    url = (f"{BASE_URL}/v3/historical-candle/intraday/"
           f"{quote(instrument_key, safe='')}/{unit}/{interval}")
    try:
        res = http_get(url, headers=_headers(), timeout=10)
        if res.status_code == 200:
            return _candles_to_df(res.json().get("data", {}).get("candles", []))
        print(f"[DATA] intraday HTTP {res.status_code}: {res.text[:160]}")
    except Exception as e:
        print(f"[DATA] intraday fetch failed: {e}")
    return pd.DataFrame()


def fetch_history_bars(instrument_key, unit, interval, lookback_days):
    """GET /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}"""
    to_d = datetime.now().strftime("%Y-%m-%d")
    fr_d = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    url = (f"{BASE_URL}/v3/historical-candle/"
           f"{quote(instrument_key, safe='')}/{unit}/{interval}/{to_d}/{fr_d}")
    try:
        res = http_get(url, headers=_headers(), timeout=15)
        if res.status_code == 200:
            return _candles_to_df(res.json().get("data", {}).get("candles", []))
        print(f"[DATA] history HTTP {res.status_code}: {res.text[:160]}")
    except Exception as e:
        print(f"[DATA] history fetch failed: {e}")
    return pd.DataFrame()


def fetch_bars(instrument_key, hcfg):
    """
    Horizon-native bars: history (past sessions) + intraday (today),
    concatenated and de-duplicated on timestamp.
    Pure-intraday horizon skips the history call.
    """
    unit, interval = hcfg["unit"], hcfg["interval"]
    parts = []
    if hcfg["lookback_days"] > 0:
        parts.append(fetch_history_bars(instrument_key, unit, interval,
                                        hcfg["lookback_days"]))
    if unit == "minutes":
        parts.append(fetch_intraday_bars(instrument_key, unit, interval))
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df = (df.drop_duplicates(subset="timestamp", keep="last")
            .sort_values("timestamp").reset_index(drop=True))
    return df


_expiry_cache = {}      # underlying_key -> (date fetched, nearest expiry)
_chain_cache = {}       # underlying_key -> (unix_ts, atm_row) ; 60 s TTL


def get_nearest_expiry(underlying_key):
    today = datetime.now().strftime("%Y-%m-%d")
    cached = _expiry_cache.get(underlying_key)
    if cached and cached[0] == today:
        return cached[1]
    try:
        res = http_get(f"{BASE_URL}/v2/option/contract", headers=_headers(),
                       params={"instrument_key": underlying_key},
                       timeout=10)
        if res.status_code == 200:
            expiries = sorted({c.get("expiry", "")
                               for c in res.json().get("data", [])
                               if c.get("expiry", "") >= today})
            if expiries:
                _expiry_cache[underlying_key] = (today, expiries[0])
                return expiries[0]
        else:
            print(f"[DATA] option/contract HTTP {res.status_code}")
    except Exception as e:
        print(f"[DATA] option/contract failed: {e}")
    return None


def fetch_atm_chain_row(underlying_key):
    """
    ATM row (strike closest to underlying_spot_price) of the nearest-expiry
    put/call chain. Cached 60 s so the four horizons share one API call.
    """
    now = time.time()
    cached = _chain_cache.get(underlying_key)
    if cached and now - cached[0] < 60:
        return cached[1]
    expiry = get_nearest_expiry(underlying_key)
    if not expiry:
        return {}
    try:
        res = http_get(f"{BASE_URL}/v2/option/chain", headers=_headers(),
                       params={"instrument_key": underlying_key,
                               "expiry_date": expiry},
                       timeout=10)
        if res.status_code != 200:
            print(f"[DATA] option/chain HTTP {res.status_code}")
            return {}
        rows = [r for r in res.json().get("data", [])
                if r.get("call_options") and r.get("put_options")]
        if not rows:
            return {}
        spot = rows[0].get("underlying_spot_price", 0.0)
        row = min(rows, key=lambda r: abs(r.get("strike_price", 0.0) - spot))
        _chain_cache[underlying_key] = (now, row)
        return row
    except Exception as e:
        print(f"[DATA] option/chain failed: {e}")
    return {}


# ==============================================================================
# MATH CORE
# ==============================================================================
def wilder_atr(df, period):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr = pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]
    return float(max(atr, 1e-9))


def ewma_realised_vol(df, ann_factor, lam=EWMA_LAMBDA):
    """RiskMetrics EWMA vol of bar log returns, annualised (%) with the
    horizon's own bar frequency."""
    c = df["close"].values.astype(float)
    if len(c) < 3:
        return 0.0
    r = np.diff(np.log(np.maximum(c, 1e-9)))
    sigma2 = r[0] ** 2
    for x in r[1:]:
        sigma2 = lam * sigma2 + (1.0 - lam) * x * x
    return float(np.sqrt(sigma2 * ann_factor) * 100.0)


def volume_zscore(df, window=VOL_Z_WINDOW):
    v = df["volume"].astype(float)
    if len(v) < window + 1:
        return 0.0
    mu = v.rolling(window).mean().iloc[-1]
    sd = v.rolling(window).std().iloc[-1]
    if not np.isfinite(sd) or sd < 1e-9:
        return 0.0
    return float((v.iloc[-1] - mu) / sd)


def momentum_tstat(df, window=MOM_WINDOW):
    """
    t-statistic of the last `window` bar log returns:
        t = mean(r) / (std(r)/sqrt(n))
    Measures momentum QUALITY: a steady drift scores higher than a single
    large spike with equal total move. Scale-free across horizons.
    """
    c = df["close"].values.astype(float)
    if len(c) < window + 1:
        return 0.0
    r = np.diff(np.log(np.maximum(c[-(window + 1):], 1e-9)))
    sd = r.std(ddof=1)
    if sd < 1e-12:
        return 0.0
    return float(r.mean() / (sd / np.sqrt(len(r))))


def variance_ratio(df, q=VR_Q, window=80):
    """
    Lo-MacKinlay variance ratio on the last `window` bar log returns:
        VR(q) = Var(r_t + ... + r_{t-q+1}) / (q * Var(r_t))
    Returns ln(VR): > 0 trending (positive autocorrelation, breakouts should
    follow through), < 0 mean-reverting (fade breakouts). Random walk -> 0.
    """
    c = df["close"].values.astype(float)
    if len(c) < max(window, 4 * q) + 1:
        return 0.0
    r = np.diff(np.log(np.maximum(c[-(window + 1):], 1e-9)))
    v1 = r.var(ddof=1)
    if v1 < 1e-16:
        return 0.0
    rq = np.convolve(r, np.ones(q), mode="valid")   # rolling q-period returns
    vq = rq.var(ddof=1)
    vr = vq / (q * v1)
    return float(np.log(max(vr, 1e-6)))


def volume_profile(df, n_bins=PROFILE_BINS, va_pct=VALUE_AREA_PCT):
    """Proportional-overlap volume profile -> POC / VAH / VAL."""
    if df.empty or len(df) < 10:
        return {"poc": 0.0, "vah": 0.0, "val": 0.0}
    lo, hi = df["low"].min(), df["high"].max()
    if hi - lo < 1e-9:
        p = float(df["close"].iloc[-1])
        return {"poc": p, "vah": p, "val": p}
    edges = np.linspace(lo, hi, n_bins + 1)
    hist = np.zeros(n_bins)
    for h, l, v in zip(df["high"].values, df["low"].values,
                       df["volume"].values):
        if h - l < 1e-9:
            idx = min(np.searchsorted(edges, l, side="right") - 1, n_bins - 1)
            hist[max(idx, 0)] += v
            continue
        ov_lo = np.maximum(edges[:-1], l)
        ov_hi = np.minimum(edges[1:], h)
        hist += v * np.clip(ov_hi - ov_lo, 0.0, None) / (h - l)
    poc_i = int(np.argmax(hist))
    poc = 0.5 * (edges[poc_i] + edges[poc_i + 1])
    total, captured = hist.sum(), hist[poc_i]
    lo_i = hi_i = poc_i
    while captured < va_pct * total:
        v_dn = hist[lo_i - 1] if lo_i > 0 else -1.0
        v_up = hist[hi_i + 1] if hi_i < n_bins - 1 else -1.0
        if v_dn < 0 and v_up < 0:
            break
        if v_up >= v_dn:
            hi_i += 1
            captured += hist[hi_i]
        else:
            lo_i -= 1
            captured += hist[lo_i]
    return {"poc": float(poc), "vah": float(edges[hi_i + 1]),
            "val": float(edges[lo_i])}


def _epoch(ts):
    """Robust epoch seconds for candle timestamps (Upstox returns +05:30
    tz-aware ISO; naive timestamps are assumed to be IST)."""
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("Asia/Kolkata")
    return float(ts.timestamp())


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35, 35)))


def kelly_fraction(p, beta):
    return max(0.0, p - (1.0 - p) / max(beta, 1e-9))


# ==============================================================================
# PER-HORIZON LEARNER (independent EG / Hedge state)
# ==============================================================================
class HorizonLearner:
    def __init__(self, horizon, hcfg):
        self.horizon = horizon
        self.eta = hcfg["eta"]
        self.p_base = hcfg["p_base"]
        self.w_init = dict(hcfg["w_init"])
        for k in FEATURES:               # V2 features start at their priors
            self.w_init.setdefault(k, V2_FEATURE_PRIORS.get(k, 0.6))
        self.w_init = {k: self.w_init[k] for k in FEATURES}
        if V2["mass_renorm"]:
            # 13 features must compete inside the ORIGINAL 8-feature
            # conviction budget — richer evidence re-ranks, never inflates.
            base_mass = sum(hcfg["w_init"].values())
            scale = base_mass / sum(self.w_init.values())
            self.w_init = {k: v * scale for k, v in self.w_init.items()}
        self.w_total = sum(self.w_init.values())
        # V2 mixture of experts: two weight books, blended by regime gate g
        # (g→1 trend book, g→0 chop book). With regime_gate off, g is pinned
        # at 0.5 and the books move in lockstep — exact v1 behaviour.
        self.w_trend = dict(self.w_init)
        self.w_chop = dict(self.w_init)
        self.p_star = hcfg["p_base"]
        self.total_trades = 0
        self.wins = 0
        self.shadow_n = 0
        self.shadow_wins = 0
        self.calib_a, self.calib_b = 1.0, 0.0   # Platt map: identity at start
        self.calib_n = 0          # outcomes (real + shadow) behind the map
        self._decay_ts = None     # last p*-idle-decay timestamp
        # earned credit per feature: cumulative eta*R*phi BEFORE clip/renorm.
        # Net dw conflates this with renormalization dilution. In-memory
        # diagnostic; not persisted.
        self.credit = {k: 0.0 for k in FEATURES}
        self._load()

    @property
    def weights(self):
        """Blended (g=0.5) view — reporting and back-compat for harnesses."""
        return {k: 0.5 * (self.w_trend[k] + self.w_chop[k])
                for k in FEATURES}

    def _load(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT weights_json, p_star, total_trades, wins, "
                "calib_a, calib_b FROM "
                "brain_state WHERE horizon=? ORDER BY id DESC LIMIT 1",
                (self.horizon,)).fetchone()
            conn.close()
            if row:
                w = json.loads(row[0])
                if isinstance(w, dict) and "trend" in w and "chop" in w:
                    wt, wc = w["trend"], w["chop"]    # v2 dual-book format
                else:
                    wt = wc = w                        # legacy flat book
                self.w_trend = {k: float(wt.get(k, self.w_init[k]))
                                for k in FEATURES}
                self.w_chop = {k: float(wc.get(k, self.w_init[k]))
                               for k in FEATURES}
                # books saved under a different mass regime (pre-renorm
                # rows) are rescaled to the current budget on load
                for book in (self.w_trend, self.w_chop):
                    s = sum(book.values())
                    if s > 1e-9 and abs(s - self.w_total) > 1e-6:
                        for k in FEATURES:
                            book[k] = book[k] * self.w_total / s
                self.p_star = float(row[1])
                self.total_trades, self.wins = int(row[2]), int(row[3])
                self.calib_n = self.total_trades
                self.calib_a = float(row[4] if row[4] is not None else 1.0)
                self.calib_b = float(row[5] if row[5] is not None else 0.0)
                print(f"[BRAIN/{self.horizon}] hydrated | "
                      f"p*={self.p_star:.3f} | calib a={self.calib_a:.2f} "
                      f"b={self.calib_b:+.2f} | record "
                      f"{self.wins}/{self.total_trades}")
        except Exception:
            pass

    def _save(self):
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO brain_state (horizon, timestamp, weights_json, "
            "p_star, total_trades, wins, calib_a, calib_b) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (self.horizon, datetime.now().isoformat(),
             json.dumps({"trend": self.w_trend, "chop": self.w_chop}),
             self.p_star, self.total_trades, self.wins,
             self.calib_a, self.calib_b))
        conn.commit()
        conn.close()

    def log_odds(self, phi, g=0.5):
        """Regime-gated fusion: effective weight per feature is the g-blend
        of the trend and chop books. Linear v1 is the g=0.5 special case."""
        if not V2["regime_gate"]:
            g = 0.5
        return BIAS0 + sum(
            (g * self.w_trend[k] + (1.0 - g) * self.w_chop[k]) * phi[k]
            for k in FEATURES)

    def calibrate(self, L):
        """Platt-calibrated posterior: p_cal = sigma(a*L + b). V2: identity
        until CALIB_MIN_N outcomes exist — a map fitted on a dozen noisy
        trades must not crush conviction (battle-test finding: a=0.47 off
        14 samples locked the horizon out)."""
        if V2["calib_warmup"] and self.calib_n < CALIB_MIN_N:
            return float(sigmoid(L))
        return float(sigmoid(self.calib_a * L + self.calib_b))

    def decay_pstar(self, now_ts):
        """V2: idle decay of the entry bar toward p_base. The loss ratchet
        still raises p* sharply; TIME, not only wins, releases it — so a
        losing streak can no longer lock the horizon out permanently
        (can't trade → can't win → can't lower the bar)."""
        if not V2["pstar_decay"]:
            return
        if self._decay_ts is None:
            self._decay_ts = now_ts
            return
        dt_days = max(0.0, (now_ts - self._decay_ts) / 86400.0)
        self._decay_ts = now_ts
        if self.p_star > self.p_base:
            self.p_star = max(self.p_base,
                              self.p_star - PSTAR_DECAY_PER_DAY * dt_days)

    # ----- shared update machinery ------------------------------------------
    def _update_books(self, R, phi, g, eta):
        for k in FEATURES:
            self.credit[k] += eta * R * phi[k]   # pre-clip/renorm credit
        for book, share in ((self.w_trend, g), (self.w_chop, 1.0 - g)):
            for k in FEATURES:
                book[k] = float(np.clip(
                    book[k] * np.exp(eta * R * phi[k] * share),
                    W_MIN, W_MAX))
            s = sum(book.values())
            if s > 1e-9:
                for k in FEATURES:
                    book[k] = book[k] * self.w_total / s

    def _update_calib(self, R, L_entry, lr):
        if L_entry is None:
            return
        y = 1.0 if R > 0 else 0.0
        p_hat = float(sigmoid(self.calib_a * L_entry + self.calib_b))
        a_min = CALIB_A_FLOOR if V2["calib_warmup"] else CALIB_A_MIN
        self.calib_a = float(np.clip(
            self.calib_a - lr * (p_hat - y) * L_entry, a_min, CALIB_A_MAX))
        self.calib_b = float(np.clip(
            self.calib_b - lr * (p_hat - y), CALIB_B_MIN, CALIB_B_MAX))
        self.calib_n += 1

    def learn(self, trade):
        """Bounded, mass-conserving EG update on the realised R-multiple —
        THIS horizon's books only, credited by the entry regime gate —
        plus p* ratchet and online Platt calibration."""
        R = float(np.clip(trade["r_multiple"], R_CLIP_LO, R_CLIP_HI))
        g = (float(trade.get("g_entry", 0.5))
             if V2["regime_gate"] else 0.5)
        self._update_books(R, trade["phi"], g, self.eta)
        if R > 0:
            self.wins += 1
            self.p_star = max(P_STAR_MIN, self.p_star - 0.005)
        else:
            self.p_star = min(P_STAR_MAX, self.p_star + 0.015)
        self._update_calib(R, trade.get("L_entry"), CALIB_LR)
        self.total_trades += 1
        self._save()
        print(f"[LEARN/{self.horizon}] R={R:+.2f} -> p*={self.p_star:.3f} | "
              f"calib a={self.calib_a:.2f} b={self.calib_b:+.2f} | "
              f"w={ {k: round(v, 2) for k, v in self.weights.items()} }")

    def learn_shadow(self, shadow, scale=1.0):
        """V2 counterfactual learning: a below-p* (or vetoed) evaluation
        that passed every other hard gate, resolved by the same barrier
        walk. Same machinery, reduced authority (eta x0.25, calib lr x0.5),
        further scaled by 1/sqrt(universe size) so shadow VOLUME — which
        grows with symbol count — cannot stampede the shared per-horizon
        p*/calibration state. Winning shadows lower p* (the recovery
        channel that lets conviction return WHILE FLAT); losing shadows
        gently confirm the bar."""
        R = float(np.clip(shadow["r_multiple"], R_CLIP_LO, R_CLIP_HI))
        g = (float(shadow.get("g_entry", 0.5))
             if V2["regime_gate"] else 0.5)
        self._update_books(R, shadow["phi"], g,
                           self.eta * SHADOW_ETA_SCALE * scale)
        if R > 0:
            self.shadow_wins += 1
            self.p_star = max(P_STAR_MIN,
                              self.p_star - SHADOW_PSTAR_WIN * scale)
        else:
            self.p_star = min(P_STAR_MAX,
                              self.p_star + SHADOW_PSTAR_LOSS * scale)
        self._update_calib(R, shadow.get("L_entry"), CALIB_LR * 0.5 * scale)
        self.shadow_n += 1
        if self.shadow_n % 20 == 0:
            self._save()


# ==============================================================================
# THE OMNIHORIZON BRAIN
# ==============================================================================
class OmniBrain:
    def __init__(self):
        self.learners = {h: HorizonLearner(h, cfg)
                         for h, cfg in HORIZONS.items()}
        self.active = db_load_positions()      # (horizon, symbol) -> trade
        if self.active:
            print(f"[BRAIN] re-hydrated {len(self.active)} open position(s) "
                  f"from disk: {sorted(self.active.keys())}")
        self.last_L = {}                       # (horizon, symbol) -> raw L
        self.last_eval = {}                    # (horizon, symbol) -> unix ts
        self.symbol_cfg = {}                   # symbol -> universe cfg (for
                                               # thesis re-scoring at barriers)
        self.cooldown_until = {}               # (horizon, symbol) -> unix ts
        self.pcr_hist = {}                     # symbol -> [(ts, -ln PCR level)]
                                               # rolling window for PCR_MODE
                                               # "delta" (fresh positioning)
        self.pcr_mad = {}                      # symbol -> EWMA |delta| (the
                                               # self-normalizer for x7)
        self.shadows = {}                      # (horizon, symbol) -> shadow
        self.shadow_next_ok = {}               # (horizon, symbol) -> unix ts
        self.regime_s = {}                     # (horizon, symbol) -> smoothed
                                               # x5 for the V2 regime gate g
        self.align_cache = {}                  # (slow_h, symbol) ->
                                               # (unix_ts, align value)

    # ----- evidence -------------------------------------------------------
    def extract_features(self, df, cfg, hcfg, key=None):
        atr = wilder_atr(df, hcfg["atr_period"])
        close = float(df["close"].iloc[-1])
        prof = volume_profile(df)
        x = {
            "structure": (close - prof["poc"]) / atr,
            "value":     (close - prof["vah"]) / atr,
            "vol_anom":  volume_zscore(df),
            "mom_t":     momentum_tstat(df),
            "vratio":    variance_ratio(df),
            "flow": 0.0, "pcr": 0.0, "vrp": 0.0,
            "maturity": 0.0, "accel": 0.0,
            "align": 0.0, "mom_l": 0.0, "mom_v": 0.0,
        }
        # regime gate g first — the trend-pack features condition on it
        g = 0.5
        if V2["regime_gate"]:
            raw = x["vratio"]
            if key is not None:
                prev = self.regime_s.get(key)
                raw = (raw if prev is None
                       else REGIME_EWMA * prev + (1.0 - REGIME_EWMA) * raw)
                self.regime_s[key] = raw
            g = float(sigmoid(REGIME_GATE_K * raw))

        if V2["new_features"]:
            # x9 maturity — regime-conditional (trend pack). Chop mode:
            # position in the long range (+ near base = recovery fuel,
            # - extended = exhaustion). Trend mode: pullback depth from the
            # recent swing high — at/near highs is GOOD in a trend (52-week-
            # high effect), deep breakdowns are not. Blended by g, so trend
            # strength decides which reading applies.
            n_anc = min(len(df), MATURITY_WINDOW)
            hh = float(df["high"].iloc[-n_anc:].max())
            ll = float(df["low"].iloc[-n_anc:].min())
            x_chop = (2.0 * (1.0 - 2.0 * (close - ll) / (hh - ll))
                      if hh - ll > 1e-9 else 0.0)
            if V2["trend_pack"] and atr and atr > 0:
                n_pb = min(len(df), PULLBACK_WINDOW)
                hh_pb = float(df["high"].iloc[-n_pb:].max())
                x_trend = 1.5 - PULLBACK_ATR_SCALE * (hh_pb - close) / atr
                x["maturity"] = g * x_trend + (1.0 - g) * x_chop
            else:
                x["maturity"] = x_chop
            # x11 acceleration: the CHANGE of momentum quality — the turn
            # detector. The t-stat level is late by design; its delta fires
            # at the inflection (same level→delta lesson as ΔPCR).
            if len(df) > MOM_WINDOW + ACCEL_LAG:
                x["accel"] = (momentum_tstat(df)
                              - momentum_tstat(df.iloc[:-ACCEL_LAG]))

        if V2["trend_pack"]:
            # x14 momentum spectrum: a second, 5x-slower t-stat. Fast and
            # slow momentum become separate learnable voices, making the
            # best configuration — "shallow pullback inside a long advance"
            # — visible to the fusion for the first time.
            if len(df) > MOM_L_MULT * MOM_WINDOW:
                x["mom_l"] = momentum_tstat(df,
                                            window=MOM_L_MULT * MOM_WINDOW)
            # x16 volume-confirmed momentum: the interaction a linear pooler
            # cannot form — drift on expanding participation persists; on
            # fading volume it is silent (0), never penalised.
            x["mom_v"] = x["mom_t"] * float(np.clip(x["vol_anom"],
                                                    0.0, 2.0)) / 2.0
            # x12 HTF alignment: the next-slower horizon's trend, as
            # evidence with a learnable weight up to 2.5 — "trend is your
            # friend" promoted from a ±0.30 whisper to a first-class voice.
            if key is not None:
                x["align"] = self._align_state(key, cfg, df)

        meta = {"atr": atr, "close": close, "iv_gate_ok": True,
                "ce_iv": None, "atm_strike": None, "expiry": None,
                "options_active": False, "g": g}

        if cfg.get("has_options") and cfg.get("instrument_key"):
            row = fetch_atm_chain_row(cfg["instrument_key"])
            if row:
                meta["options_active"] = True
                ce_md = row["call_options"].get("market_data", {})
                pe_md = row["put_options"].get("market_data", {})
                ce_oi = ce_md.get("oi") or 0.0
                pe_oi = pe_md.get("oi") or 0.0
                pcr_level = -np.log(max((pe_oi + 1.0) / (ce_oi + 1.0), 1e-6))
                if PCR_MODE == "delta":
                    # OI accumulates -> the LEVEL is a saturating integral of
                    # past flow (near-constant at entries). The signal is in
                    # fresh positioning: windowed change of -ln(PCR).
                    x["pcr"] = self._pcr_delta(cfg["symbol"], pcr_level)
                else:
                    x["pcr"] = pcr_level
                bid_q = ce_md.get("bid_qty") or 0.0
                ask_q = ce_md.get("ask_qty") or 0.0
                x["flow"] = np.log((bid_q + 1.0) / (ask_q + 1.0))
                ce_iv = (row["call_options"].get("option_greeks", {})
                         .get("iv") or 0.0)
                meta["ce_iv"] = ce_iv
                rv = ewma_realised_vol(df, hcfg["ann_factor"])
                if ce_iv > 1e-6:
                    x["vrp"] = (rv - ce_iv) / ce_iv
                meta["iv_gate_ok"] = ce_iv < cfg["iv_cap"]
                meta["atm_strike"] = row.get("strike_price")
                meta["expiry"] = row.get("expiry")

        phi = {k: float(np.tanh(v / 2.0)) for k, v in x.items()}
        return x, phi, meta

    # ----- x12: higher-timeframe trend alignment ------------------------------
    def _align_state(self, key, cfg, df):
        """Trend state of the NEXT-SLOWER horizon (its momentum t-stat on
        native bars). Positional anchors on its own secular drift. Cached
        per symbol at the source's cadence, so the live API cost rounds to
        zero; in replay the patched fetch_bars serves slices for free."""
        horizon, symbol = key
        slow = ALIGN_SOURCE.get(horizon)
        if slow is None:                          # positional: secular drift
            if len(df) > SECULAR_WINDOW:
                return float(momentum_tstat(df, window=SECULAR_WINDOW))
            return 0.0
        ck = (slow, symbol)
        now = time.time()
        hit = self.align_cache.get(ck)
        if hit and now - hit[0] < ALIGN_TTL_S[slow]:
            return hit[1]
        val = 0.0
        ikey = cfg.get("instrument_key")
        if ikey:
            sdf = fetch_bars(ikey, HORIZONS[slow])
            if not sdf.empty and len(sdf) > MOM_WINDOW:
                val = float(momentum_tstat(sdf))
        self.align_cache[ck] = (now, val)
        return val

    # ----- PCR delta (fresh positioning) ------------------------------------
    def _pcr_delta(self, symbol, level):
        """Windowed change of -ln(PCR) — level now minus the oldest sample
        within PCR_DELTA_WINDOW_S — self-normalized by an EWMA of its own
        absolute size. Scale-free across symbols/regimes and immune to the
        1/OI shrink of ln-deltas as absolute OI grows; magnitude reads as
        "multiples of typical 30-min positioning change". First observation
        -> 0 (neutral until a baseline exists). Uses time.time(), so it runs
        correctly under the backtest's simulated clock."""
        now = time.time()
        hist = self.pcr_hist.setdefault(symbol, [])
        hist.append((now, level))
        cutoff = now - PCR_DELTA_WINDOW_S
        while len(hist) > 1 and hist[0][0] < cutoff:
            hist.pop(0)
        delta = level - hist[0][1]
        mad = self.pcr_mad.get(symbol, 0.0)
        mad = (PCR_MAD_LAMBDA * mad + (1.0 - PCR_MAD_LAMBDA) * abs(delta)
               if mad > 0.0 else abs(delta))
        self.pcr_mad[symbol] = mad
        if mad < 1e-9:
            return 0.0
        return delta / mad

    # ----- cross-horizon coupling ------------------------------------------
    def coupled_log_odds(self, horizon, symbol, L_raw):
        """L'_h = L_h + KAPPA * tanh(mean of other horizons' cached L / 2)"""
        self.last_L[(horizon, symbol)] = L_raw
        others = [v for (h, s), v in self.last_L.items()
                  if s == symbol and h != horizon]
        if not others:
            return L_raw
        return L_raw + KAPPA * float(np.tanh(np.mean(others) / 2.0))

    @staticmethod
    def entry_p_star(learner, meta):
        """Adaptive p* when the options chain feeds flow/pcr. Without the
        options plane (cash-only symbol or failed chain fetch) the bar is
        never EASIER than the learner's current p*, with P_STAR_NO_OPTIONS
        as a true floor — thinner/degraded evidence must never lower entry
        strictness."""
        if meta.get("options_active"):
            return learner.p_star
        return max(learner.p_star, P_STAR_NO_OPTIONS)

    # ----- entry ------------------------------------------------------------
    def evaluate_entry(self, horizon, symbol, df, cfg):
        self.symbol_cfg[symbol] = cfg   # remembered for re-underwriting
        if not HORIZON_ENABLED.get(horizon, False):
            return                      # disabled horizon: no new entries
        hcfg = HORIZONS[horizon]
        key = (horizon, symbol)
        if key in self.active:
            _gate(horizon, "already_open")
            return
        if df.empty or len(df) < hcfg["min_bars"]:
            _gate(horizon, "min_bars")
            return
        _gate(horizon, "evaluated")
        x, phi, meta = self.extract_features(df, cfg, hcfg, key=key)
        learner = self.learners[horizon]
        learner.decay_pstar(time.time())     # V2: idle release of the ratchet
        L_raw = learner.log_odds(phi, meta.get("g", 0.5))
        L = self.coupled_log_odds(horizon, symbol, L_raw)
        p_raw = float(sigmoid(L))
        p = learner.calibrate(L)        # Platt-calibrated posterior drives
        p_thresh = self.entry_p_star(learner, meta)
        self.last_eval[key] = time.time()   # every downstream decision

        opts_tag = "" if meta.get("options_active") else ", no-opts"
        print(f"[{horizon:>10s}] {symbol:10s} p={p:.3f} "
              f"(raw={p_raw:.3f}, p*={p_thresh:.3f}{opts_tag}, "
              f"a={learner.calib_a:.2f} b={learner.calib_b:+.2f}) | "
              + " ".join(f"{k}={phi[k]:+.2f}" for k in FEATURES))

        if p < p_thresh:
            _gate(horizon, "p_star")
            # V2: the declined entry may still teach — register a shadow if
            # every HARD gate would have passed (counterfactual learning).
            self._maybe_shadow(horizon, symbol, hcfg, p, meta, phi, L, df)
            return
        # --- alignment veto (hard gate #7, earned by battery evidence) -----
        # No conviction may outvote a decisively falling higher-timeframe
        # trend. The vetoed entry registers as a shadow, so every run keeps
        # measuring what the blocked cohort would have done — if the market
        # proves the veto wrong, the report will say so in numbers.
        if V2["align_veto"] and phi.get("align", 0.0) < MISALIGN_PHI:
            _gate(horizon, "align_veto")
            print(f"[ALIGN-VETO/{horizon}] {symbol}: p={p:.3f} clears p* "
                  f"but HTF trend φ={phi['align']:+.2f} < {MISALIGN_PHI} "
                  f"— long refused (shadowed)")
            self._maybe_shadow(horizon, symbol, hcfg, p, meta, phi, L, df)
            return
        if not meta["iv_gate_ok"]:
            _gate(horizon, "iv_cap")
            return

        # post-exit cooldown: don't churn back into the same decaying setup
        if time.time() < self.cooldown_until.get(key, 0.0):
            _gate(horizon, "cooldown")
            return

        atr, entry = meta["atr"], meta["close"]
        beta = hcfg["rt"] / hcfg["rs"]
        stop_dist = hcfg["rs"] * atr

        # --- minimum profit target (skip if rt×ATR/entry below horizon floor) ---
        min_profit = hcfg.get("min_profit_pct")
        if min_profit is not None and entry > 0:
            atr_target_pct = hcfg["rt"] * atr / entry
            if atr_target_pct < min_profit:
                print(f"[MIN-PT/{horizon}] {symbol}: ATR target "
                      f"{atr_target_pct * 100:.2f}% < {min_profit * 100:.1f}% "
                      f"floor (ATR={atr:.2f}, E={entry:.2f}) — skipped")
                _gate(horizon, "min_profit")
                return

        # --- cost-aware gate: edge must survive round-trip costs -----------
        cost_R = (2.0 * COST_BPS_ROUNDTRIP / 1e4) * entry / max(stop_dist, 1e-9)
        net_eR = p * beta - (1.0 - p) - cost_R
        if net_eR < MIN_NET_EDGE_R:
            print(f"[COST-GATE/{horizon}] {symbol}: p={p:.3f} clears p* but "
                  f"net edge {net_eR:+.2f}R < {MIN_NET_EDGE_R}R "
                  f"(cost={cost_R:.2f}R at ATR {atr:.2f}) — skipped")
            _gate(horizon, "cost_gate")
            return

        f_star = kelly_fraction(p, beta)
        f_used = min(KELLY_FRACTION * f_star, RISK_CAP)
        if f_used <= 0.0:
            _gate(horizon, "kelly_zero")
            return
        _gate(horizon, "entered")
        if V2["trend_pack"] and phi.get("align", 0.0) < MISALIGN_PHI:
            _gate(horizon, "entered_misaligned")   # tag-and-measure: do
            # misaligned longs lose here? The report answers with data
            # before any hard veto is considered.
        sleeve_capital = CAPITAL * hcfg["sleeve"]
        qty = max(1, int(f_used * sleeve_capital / stop_dist))

        t = {
            "horizon": horizon, "symbol": symbol,
            "entry_time": datetime.now().isoformat(),
            "entry_ts": time.time(),
            "entry_price": entry,
            "target": entry + hcfg["rt"] * atr,
            "sl": entry - stop_dist,
            "initial_sl": entry - stop_dist,
            "atr": atr, "hwm": entry,
            "trail_armed": False,
            # V2 trail_late: never arm before ~55% of the PT distance — the
            # battle-test showed early arming harvested +0.26R scraps while
            # losses ran a full -1R (18 of 36 exits TRAILED at 11% of PT).
            "trail_arm_level": entry + (
                max(hcfg["arm_atr"], TRAIL_ARM_PT_FRAC * hcfg["rt"]) * atr
                if V2["trail_late"] else hcfg["arm_atr"] * atr),
            "trail_dist": hcfg["trail_atr"] * atr,
            "qty": qty, "p_entry": p, "p_raw": p_raw, "L_entry": float(L),
            "g_entry": float(meta.get("g", 0.5)),
            "kelly_f": f_used, "phi": phi,
            "extensions": 0,    # hold windows granted by re-underwriting
            # last 1-min bar already processed; reconciliation resumes here
            "last_bar_epoch": _epoch(df["timestamp"].iloc[-1]),
        }
        self.active[key] = t
        db_save_position(t)
        hold_u, hold_n = hcfg["max_hold"]
        print(f"\n🚀 [{horizon.upper()} ENTRY] {symbol} | p={p:.3f} "
              f"f={f_used:.4f} ({qty} units of sleeve "
              f"₹{sleeve_capital:,.0f}) | E={entry:.2f} "
              f"PT={t['target']:.2f} ({(t['target']/entry-1)*100:.2f}%) "
              f"SL={t['sl']:.2f} | "
              f"time-stop {hold_n}{hold_u}\n")

    # ----- V2 shadow trades: counterfactual learning --------------------------
    def _maybe_shadow(self, horizon, symbol, hcfg, p, meta, phi, L, df):
        """Register a shadow when an evaluation fails ONLY the p* gate.
        The shadow is walked by the same pessimistic barrier engine (plain
        time stop, no extensions), then feeds the learner at reduced
        authority. This converts ~36 lifetime lessons into thousands and
        gives a locked-out horizon a way to discover it is wrong while flat."""
        if not V2["shadow"]:
            return
        key = (horizon, symbol)
        if key in self.shadows or df.empty:
            return
        now = time.time()
        if now < self.shadow_next_ok.get(key, 0.0):
            return
        if not meta["iv_gate_ok"] or now < self.cooldown_until.get(key, 0.0):
            return
        atr, entry = meta["atr"], meta["close"]
        if not (atr and atr > 0 and entry > 0):
            return
        beta = hcfg["rt"] / hcfg["rs"]
        stop_dist = hcfg["rs"] * atr
        mp = hcfg.get("min_profit_pct")
        if mp is not None and hcfg["rt"] * atr / entry < mp:
            return
        cost_R = (2.0 * COST_BPS_ROUNDTRIP / 1e4) * entry / max(stop_dist,
                                                                1e-9)
        if p * beta - (1.0 - p) - cost_R < MIN_NET_EDGE_R:
            return
        if kelly_fraction(p, beta) <= 0.0:
            return
        arm_mult = (max(hcfg["arm_atr"], TRAIL_ARM_PT_FRAC * hcfg["rt"])
                    if V2["trail_late"] else hcfg["arm_atr"])
        self.shadows[key] = {
            "horizon": horizon, "symbol": symbol, "entry_ts": now,
            "entry_price": entry,
            "target": entry + hcfg["rt"] * atr,
            "sl": entry - stop_dist, "initial_sl": entry - stop_dist,
            "hwm": entry, "trail_armed": False,
            "trail_arm_level": entry + arm_mult * atr,
            "trail_dist": hcfg["trail_atr"] * atr,
            "phi": phi, "L_entry": float(L),
            "g_entry": float(meta.get("g", 0.5)),
            "last_bar_epoch": _epoch(df["timestamp"].iloc[-1]),
        }
        self.shadow_next_ok[key] = (
            now + hcfg["eval_every_min"] * 60 * SHADOW_REG_GAP_EVALS)
        _gate(horizon, "shadow_open")

    def _walk_shadow(self, key, sh, hcfg, bar, ep):
        """One 1-min bar against a shadow: same pessimistic ordering as the
        real walk, plain time stop, no extensions, no orders, no DB."""
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        bar_dt = datetime.fromtimestamp(ep, IST)
        sq = hcfg["square_off"]
        if sq:
            entry_d = datetime.fromtimestamp(sh["entry_ts"], IST).date()
            if bar_dt.date() > entry_d or bar_dt.strftime("%H:%M") >= sq:
                return self._close_shadow(key, o)
        unit, n = hcfg["max_hold"]
        limit = n * 60 if unit == "min" else n * 86400
        if ep - sh["entry_ts"] >= limit:
            return self._close_shadow(key, o)
        if l <= sh["sl"]:
            return self._close_shadow(key, min(sh["sl"], o))
        if h >= sh["target"]:
            return self._close_shadow(key, max(sh["target"], o))
        if h > sh["hwm"]:
            sh["hwm"] = h
        if not sh["trail_armed"] and h >= sh["trail_arm_level"]:
            sh["trail_armed"] = True
        if sh["trail_armed"]:
            new_sl = sh["hwm"] - sh["trail_dist"]
            if new_sl > sh["sl"]:
                sh["sl"] = new_sl
        return False

    def _close_shadow(self, key, exit_price):
        sh = self.shadows.pop(key)
        risk = sh["entry_price"] - sh["initial_sl"]
        sh["r_multiple"] = ((exit_price - sh["entry_price"]) / risk
                            if risk > 1e-9 else 0.0)
        _gate(key[0], "shadow_win" if sh["r_multiple"] > 0
              else "shadow_loss")
        # shadow volume scales with universe size while p*/calibration are
        # per-horizon SHARED state — normalise step authority by sqrt(N) so
        # 100 symbols' shadows cannot stampede the bar (audit finding)
        n_sym = max(1, len(self.symbol_cfg))
        self.learners[key[0]].learn_shadow(sh, scale=1.0 / np.sqrt(n_sym))
        return True

    # ----- exits: bar-walk reconciliation engine -----------------------------
    # Every exit decision is made by replaying 1-minute bars NEWER than the
    # position's last processed bar. On a healthy cycle that is 1-2 bars; after
    # a network outage or a process restart it is the entire gap — so trail
    # updates, barrier hits and square-offs land on the price path that
    # actually happened, never on the post-gap price alone.
    def reconcile_and_manage(self, symbol, bars_df):
        if bars_df.empty:
            return
        epochs = bars_df["timestamp"].map(_epoch).values
        for key in [k for k in list(self.active) if k[1] == symbol]:
            t = self.active[key]
            hcfg = HORIZONS[key[0]]
            start = np.searchsorted(epochs,
                                    t.get("last_bar_epoch", t["entry_ts"]),
                                    side="right")
            gap = len(bars_df) - start
            if gap > 5:
                print(f"[RECONCILE/{key[0]}] {symbol}: replaying {gap} "
                      f"missed bars after disruption/restart")
            closed = False
            for i in range(start, len(bars_df)):
                t["last_bar_epoch"] = float(epochs[i])
                if self._walk_bar(key, t, hcfg, bars_df.iloc[i], epochs[i]):
                    closed = True
                    break
            if not closed and gap > 0:
                db_save_position(t)   # persist trail/hwm/last_bar mutations

        # V2 shadows ride the same bars (in-memory, no orders, no DB)
        if V2["shadow"]:
            for key in [k for k in list(self.shadows) if k[1] == symbol]:
                sh = self.shadows[key]
                hcfg = HORIZONS[key[0]]
                start = np.searchsorted(
                    epochs, sh.get("last_bar_epoch", sh["entry_ts"]),
                    side="right")
                for i in range(start, len(bars_df)):
                    sh["last_bar_epoch"] = float(epochs[i])
                    if self._walk_shadow(key, sh, hcfg, bars_df.iloc[i],
                                         epochs[i]):
                        break

    def _walk_bar(self, key, t, hcfg, bar, ep):
        """Process one 1-min bar against an open position. Returns True if
        the position closed. Pessimistic intra-bar ordering: square-off and
        time barrier first (at the bar open), then stop before target; fills
        respect gaps (gap through a barrier fills at the bar open, never at
        the barrier price the market skipped over)."""
        horizon, symbol = key
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        bar_dt = datetime.fromtimestamp(ep, IST)   # IST regardless of host TZ

        # 1) intraday square-off: at/after cutoff, or any later session
        sq = hcfg["square_off"]
        if sq:
            entry_d = datetime.fromtimestamp(t["entry_ts"], IST).date()
            if bar_dt.date() > entry_d or bar_dt.strftime("%H:%M") >= sq:
                self._close(key, o, "SQUARE_OFF")
                return True

        # 2) time barrier, evaluated at this bar's open — CONDITIONAL:
        #    re-underwrite the thesis instead of obeying a dumb clock.
        unit, n = hcfg["max_hold"]
        limit = n * 60 if unit == "min" else n * 86400
        allowed = limit * (1 + t.get("extensions", 0))
        if ep - t["entry_ts"] >= allowed:
            if self._earns_extension(key, t, hcfg, ep):
                t["extensions"] = t.get("extensions", 0) + 1
                db_save_position(t)
                print(f"⏳ [EXTEND/{key[0]}] {key[1]} hold window "
                      f"{t['extensions']}/{hcfg.get('max_extensions', 0)} "
                      f"granted")
            else:
                self._close(key, o, "TIME_EXIT")
                return True

        # 3) stop barrier first (pessimistic), gap-realistic fill
        if l <= t["sl"]:
            fill = min(t["sl"], o)
            outcome = ("TRAILED" if t["trail_armed"]
                       and t["sl"] > t["initial_sl"] else "LOSS")
            self._close(key, fill, outcome)
            return True

        # 4) target barrier, favourable gap fills at the open
        if h >= t["target"]:
            self._close(key, max(t["target"], o), "PROFIT")
            return True

        # 5) bar survived: update high-water mark and chandelier trail
        if h > t["hwm"]:
            t["hwm"] = h
        if not t["trail_armed"] and h >= t["trail_arm_level"]:
            t["trail_armed"] = True
            print(f"🔒 [TRAIL/{horizon}] armed for {symbol} at {h:.2f}")
        if t["trail_armed"]:
            new_sl = t["hwm"] - t["trail_dist"]
            if new_sl > t["sl"]:
                t["sl"] = new_sl
        return False

    # ----- re-underwriting time stop ------------------------------------------
    def _earns_extension(self, key, t, hcfg, ep):
        """
        Decide whether a position at its time barrier deserves another hold
        window. Replaces 'time elapsed' with 'information decayed':

          1. Extension budget exhausted -> no.
          2. Live edge (bar is current):  re-score the thesis with FRESH
             evidence; extend iff p_now >= P_HOLD. The market, not the
             clock, decides.
          3. Gap replay / data unavailable: failure-to-perform fallback —
             extend iff the trade ever progressed >= PROGRESS_MIN_R of its
             risk (a moving trade keeps its slot; a sideways zombie is cut).

        The intraday 15:15 square-off is checked BEFORE this and remains a
        hard cap, so labels stay bounded regardless of extensions.
        """
        horizon, symbol = key
        if t.get("extensions", 0) >= hcfg.get("max_extensions", 0):
            return False

        if time.time() - ep <= LIVE_EDGE_SLACK:
            p_now = self._rescore(horizon, symbol)
            if p_now is not None:
                keep = p_now >= P_HOLD
                print(f"[RE-UNDERWRITE/{horizon}] {symbol}: thesis re-scored "
                      f"p={p_now:.3f} vs hold {P_HOLD} -> "
                      f"{'EXTEND' if keep else 'EXIT'}")
                return keep

        risk = t["entry_price"] - t["initial_sl"]
        prog = (t["hwm"] - t["entry_price"]) / risk if risk > 1e-9 else 0.0
        keep = prog >= PROGRESS_MIN_R
        print(f"[TIME-CHECK/{horizon}] {symbol}: best progress {prog:+.2f}R "
              f"vs {PROGRESS_MIN_R}R -> {'EXTEND' if keep else 'EXIT'} "
              f"(fallback: no fresh evidence)")
        return keep

    def _rescore(self, horizon, symbol):
        """Recompute the fused posterior for (horizon, symbol) from fresh
        horizon-native bars. Returns None when evidence is unavailable."""
        cfg = self.symbol_cfg.get(symbol)
        if not cfg or not cfg.get("instrument_key"):
            return None
        hcfg = HORIZONS[horizon]
        df = fetch_bars(cfg["instrument_key"], hcfg)
        if df.empty or len(df) < hcfg["min_bars"]:
            return None
        _, phi, meta = self.extract_features(df, cfg, hcfg,
                                             key=(horizon, symbol))
        learner = self.learners[horizon]
        L = self.coupled_log_odds(
            horizon, symbol, learner.log_odds(phi, meta.get("g", 0.5)))
        return learner.calibrate(L)     # calibrated, same scale as P_HOLD

    def _close(self, key, exit_price, outcome):
        horizon, symbol = key
        t = self.active.pop(key)
        db_delete_position(horizon, symbol)
        self.cooldown_until[key] = (time.time()
                                    + COOLDOWN_MIN.get(horizon, 0) * 60)
        risk = t["entry_price"] - t["initial_sl"]
        pnl = (exit_price - t["entry_price"]) * t["qty"]
        r_mult = (exit_price - t["entry_price"]) / risk if risk > 1e-9 else 0.0
        t.update({"exit_price": exit_price, "outcome": outcome,
                  "exit_time": datetime.now().isoformat(),
                  "pnl": pnl, "r_multiple": r_mult})
        db_log_trade(t)
        self.learners[horizon].learn(t)     # only THIS horizon learns
        icon = "✅" if r_mult > 0 else "❌"
        print(f"{icon} [{outcome}/{horizon}] {symbol} exit {exit_price:.2f} "
              f"| R={r_mult:+.2f} | PnL={pnl:+.2f}")

    # ----- scheduling ----------------------------------------------------------
    def due(self, horizon, symbol):
        last = self.last_eval.get((horizon, symbol), 0.0)
        return time.time() - last >= HORIZONS[horizon]["eval_every_min"] * 60


# ==============================================================================
# MAIN LOOP
# ==============================================================================
def run():
    enabled = [h for h in HORIZONS if HORIZON_ENABLED.get(h, False)]
    print("=" * 78)
    print("  HERMES OMNIHORIZON — MULTI-HORIZON BAYESIAN CONFLUENCE ENGINE (NSE)")
    print("  4 horizons x 8 evidence features | per-horizon EG learning |")
    print("  cross-horizon coupling | Kelly sleeves | crash-safe positions")
    print(f"  ENABLED HORIZONS: {', '.join(enabled) if enabled else 'NONE'}")
    print("=" * 78)
    if not enabled:
        print("[WARN] all horizons disabled in HORIZON_ENABLED — engine will "
              "only manage/exit existing open positions.")
    init_database()
    print(f"  DB: {DB_PATH}  (fresh brain — p* from HORIZONS p_base, not old state)")
    brain = OmniBrain()
    # register universe up-front so hydrated positions can be re-underwritten
    # at their time barriers even before any entry evaluation has run
    for token, ucfg in UNIVERSE.items():
        brain.symbol_cfg[ucfg["symbol"]] = {**ucfg, "instrument_key": token}
    net_down = False            # network disruption flag

    while True:
        now = datetime.now(IST)
        now_hm = now.strftime("%H:%M")
        in_session = "09:15" <= now_hm <= "15:30"

        if in_session:
            cycle_ok = False
            for token, cfg in UNIVERSE.items():
                cfg = {**cfg, "instrument_key": token}
                symbol = cfg["symbol"]

                # --- demand-aware polling (rate-limit hygiene at scale) ---
                # An idle symbol (no open position, no horizon due) needs no
                # data this cycle. With ~100 symbols this is the difference
                # between ~3000 and ~500 requests per 30 min. Exits are never
                # starved: any symbol with an open position is fetched every
                # cycle; the bar-walk replays anything missed regardless.
                has_open = any(k[1] == symbol for k in brain.active)
                due_hs = [h for h, hc in HORIZONS.items()
                          if HORIZON_ENABLED.get(h, False)
                          and not (hc["square_off"]
                                   and now_hm >= hc["square_off"])
                          and brain.due(h, symbol)]
                if not has_open and not due_hs:
                    continue                    # idle: zero API spend

                # latest 1-min bars drive ALL horizons' exits
                spot_df = fetch_intraday_bars(token)
                if spot_df.empty:
                    if not net_down:
                        print("[NETWORK] data fetch failing — halting signal "
                              "generation; will reconcile on recovery")
                    net_down = True
                    continue
                cycle_ok = True
                if net_down:
                    print(f"[NETWORK] recovered — reconciling open positions "
                          f"over the gap ({symbol} first)")

                # multi-day gap (long outage or restart): today's intraday
                # bars don't reach back to the last processed bar, so
                # backfill 1-min history to cover the hole before walking.
                bars = spot_df
                open_epochs = [t.get("last_bar_epoch", t["entry_ts"])
                               for k, t in brain.active.items()
                               if k[1] == symbol]
                if open_epochs:
                    oldest = min(open_epochs)
                    if oldest < _epoch(spot_df["timestamp"].iloc[0]):
                        days = min(int((time.time() - oldest) // 86400) + 1, 30)
                        hist = fetch_history_bars(token, "minutes", "1", days)
                        if not hist.empty:
                            bars = (pd.concat([hist, spot_df])
                                    .drop_duplicates(subset="timestamp")
                                    .sort_values("timestamp")
                                    .reset_index(drop=True))

                brain.reconcile_and_manage(symbol, bars)

                # entries: each due ENABLED horizon (precomputed above) on
                # its own cadence + native bars. No fresh entries while the
                # network is degraded.
                for horizon in due_hs:
                    hcfg = HORIZONS[horizon]
                    df = (spot_df if horizon == "intraday"
                          else fetch_bars(token, hcfg))
                    brain.evaluate_entry(horizon, symbol, df, cfg)

            if cycle_ok:
                net_down = False
            time.sleep(60)
        else:
            print(f"[STANDBY] {now_hm} outside 09:15–15:30 IST window.")
            time.sleep(300)


if __name__ == "__main__":
    run()
