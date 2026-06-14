"""
PriceActionAgent — Central Configuration
=========================================
All tunable parameters in one place. Override via environment variables.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR   = Path(__file__).resolve().parent
REPO_DIR   = BASE_DIR.parent                      # SniperAgent/ (shared root)


def _data_dir() -> Path:
    """
    SQLite-safe data root. Prefer Data.nosync/ (local, WAL-safe) over synced Data/.
    Override: PA_DATA_DIR=/path
    """
    if os.environ.get("PA_DATA_DIR"):
        return Path(os.environ["PA_DATA_DIR"])
    nosync = REPO_DIR / "Data.nosync"
    if nosync.is_dir():
        return nosync
    return REPO_DIR / "Data"


DATA_DIR   = _data_dir()                          # shared data root

# Logs go under Data/PriceActionAgent/logs — no runtime files in source tree
LOG_DIR    = Path(os.environ.get("PA_LOG_DIR",
                                 str(DATA_DIR / "PriceActionAgent" / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Shared market data DB — written by RealBackTest fetch, read by all agents
MARKET_DATA_DB = Path(os.environ.get(
    "MARKET_DATA_DB",
    str(DATA_DIR / "marketdata.db")
))


def _sqlite_ok(d: Path) -> bool:
    """SQLite needs real file locking; synced/cloud folders often lack it."""
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".sqlite_probe"
        conn = sqlite3.connect(str(probe))
        conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
        conn.close()
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _pick_db(env_key: str, preferred: Path, fallback_name: str) -> Path:
    if os.environ.get(env_key):
        p = Path(os.environ[env_key])
        if p.exists() and p.is_dir():
            raise RuntimeError(
                f"{env_key}={p} is a directory, not a SQLite database file"
            )
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    if preferred.exists() and preferred.is_dir():
        print(
            f"[CONFIG] {preferred} is a directory, not a .db file — "
            f"using fallback (set {env_key}=<path> to override)"
        )
    elif _sqlite_ok(preferred.parent):
        preferred.parent.mkdir(parents=True, exist_ok=True)
        return preferred
    import tempfile
    alt = Path(tempfile.gettempdir()) / fallback_name / f"{fallback_name}.db"
    alt.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"[CONFIG] {preferred} cannot host SQLite (synced folder?) — "
        f"using {alt}  (override with {env_key}=<path>)"
    )
    return alt


import sqlite3  # needed for _sqlite_ok

# Price Action narrative store — lives in Data/PriceActionAgent/
PA_DB = _pick_db(
    "PA_DB",
    DATA_DIR / "PriceActionAgent" / "price_action.db",
    "price_action",
)

# ---------------------------------------------------------------------------
# Anthropic Model Configuration
# Choices: claude-haiku-4-5-20251001 | claude-sonnet-4-6 | claude-opus-4-8
# ---------------------------------------------------------------------------
ANTHROPIC_MODEL = os.environ.get(
    "PA_MODEL",
    "claude-haiku-4-5-20251001"   # cost-optimised; override with PA_MODEL=claude-sonnet-4-6
)


def cached_system(system_text: str) -> list[dict]:
    """
    Wrap a (large, static) system prompt as a cacheable content block.

    Prompt caching charges cache *reads* at ~10% of the input rate, so the
    static taxonomy/rules block is billed near-zero after the first call in a
    5-minute window. The per-symbol OHLCV stays in `messages` and is NOT cached
    (it varies every call), which is exactly what we want.

    Requires the block to exceed the model's min cacheable size (2048 tokens for
    Haiku) — the SniperAgent system prompt is well above that.
    """
    return [{
        "type": "text",
        "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }]

# Seed: use extended thinking for deeper multi-timeframe analysis
SEED_USE_EXTENDED_THINKING = os.environ.get(
    "PA_SEED_THINKING", "true"
).lower() == "true"

SEED_THINKING_BUDGET = int(os.environ.get("PA_SEED_THINKING_BUDGET", "10000"))
SEED_MAX_TOKENS    = int(os.environ.get("PA_SEED_MAX_TOKENS",  "16384"))
DAILY_MAX_TOKENS   = int(os.environ.get("PA_DAILY_MAX_TOKENS", "2048"))

# Temperature: tight anchoring to data — keep low
SEED_TEMPERATURE   = float(os.environ.get("PA_SEED_TEMP",  "0.15"))
DAILY_TEMPERATURE  = float(os.environ.get("PA_DAILY_TEMP", "0.10"))

# ---------------------------------------------------------------------------
# Rolling Synthesis Parameters
# ---------------------------------------------------------------------------
# Trading days of daily OHLCV history for every synthesis run (seed or daily)
# 90 days ≈ 4.5 months — enough for meaningful 1W, 1D, 4H back-propagation
ROLLING_DAYS = int(os.environ.get("PA_ROLLING_DAYS", "90"))

# Days per progressive-reconciliation chunk
# Each chunk = N trading days sent per API call
SEED_CHUNK_DAYS = int(os.environ.get("PA_SEED_CHUNK_DAYS", "15"))  # 3 weeks

# Legacy alias — kept for any code still importing SEED_MONTHS
SEED_MONTHS = int(os.environ.get("PA_SEED_MONTHS", "4"))

# ---------------------------------------------------------------------------
# Rolling OHLCV context windows for back-propagation
# Passed alongside each chunk / daily update so Claude validates claims
# against actual price data, not just narrative text.
# ---------------------------------------------------------------------------
# 4H: trading days of prior 4H bars per chunk (each day ≈ 2 4H bars)
ROLLING_4H_DAYS  = int(os.environ.get("PA_ROLLING_4H_DAYS",  "20"))
# 1D: daily bars of lookback per chunk / daily update
ROLLING_1D_DAYS  = int(os.environ.get("PA_ROLLING_1D_DAYS",  "60"))
# 1W: weekly bars for macro structure — set equal to seed window by default
ROLLING_1W_WEEKS = int(os.environ.get("PA_ROLLING_1W_WEEKS", "45"))

# Intervals used for analysis  (unit, interval) tuples matching marketdata.db
INTERVALS = {
    "daily":  ("days",    "1"),
    "weekly": ("weeks",   "1"),
    "30min":  ("minutes", "30"),
}

# ---------------------------------------------------------------------------
# NSE Universe — 97 symbols from marketdata.db
# Loaded dynamically; can be overridden via env PA_SYMBOLS (comma-separated)
# ---------------------------------------------------------------------------
_ENV_SYMBOLS = os.environ.get("PA_SYMBOLS", "")
if _ENV_SYMBOLS:
    NSE100_SYMBOLS = [s.strip().upper() for s in _ENV_SYMBOLS.split(",") if s.strip()]
else:
    NSE100_SYMBOLS = [
        "ABB", "ADANIENSOL", "ADANIENT", "ADANIGREEN", "ADANIPORTS",
        "ADANIPOWER", "AMBUJACEM", "APOLLOHOSP", "ASIANPAINT", "ATGL",
        "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV", "BAJAJHLDNG", "BAJFINANCE",
        "BANKBARODA", "BEL", "BERGEPAINT", "BHARTIARTL", "BHEL",
        "BOSCHLTD", "BPCL", "BRITANNIA", "CANBK", "CHOLAFIN",
        "CIPLA", "COALINDIA", "COLPAL", "DABUR", "DIVISLAB",
        "DLF", "DRREDDY", "EICHERMOT", "GAIL", "GODREJCP",
        "GRASIM", "HAL", "HAVELLS", "HCLTECH", "HDFCBANK",
        "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK",
        "ICICIGI", "ICICIPRULI", "INDIGO", "INDUSINDBK", "INFY",
        "IOC", "IRCTC", "IRFC", "ITC", "JINDALSTEL",
        "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LICI", "LODHA",
        "LT", "M&M", "MARICO", "MARUTI", "MOTHERSON",
        "NAUKRI", "NESTLEIND", "NTPC", "ONGC", "PFC",
        "PIDILITIND", "PNB", "POWERGRID", "RECLTD", "RELIANCE",
        "SBILIFE", "SBIN", "SHREECEM", "SHRIRAMFIN", "SIEMENS",
        "SRF", "SUNPHARMA", "TATACONSUM", "TATAPOWER", "TATASTEEL",
        "TCS", "TECHM", "TITAN", "TORNTPHARM", "TRENT",
        "TVSMOTOR", "ULTRACEMCO", "UNITDSPR", "VBL", "VEDL",
        "WIPRO", "ZYDUSLIFE",
    ]

# ---------------------------------------------------------------------------
# NSE Market Calendar
# ---------------------------------------------------------------------------
# Market closes at 15:30 IST; EOD update should run after 16:00 IST
NSE_CLOSE_HOUR_IST = 15
NSE_CLOSE_MIN_IST  = 30

# 2025 & 2026 NSE holidays (trading holidays — market closed)
# Source: NSE official holiday calendar
NSE_HOLIDAYS = {
    # 2025
    "2025-01-26",  # Republic Day
    "2025-02-26",  # Mahashivratri
    "2025-03-14",  # Holi
    "2025-03-31",  # Id-Ul-Fitr (Ramzan Eid)
    "2025-04-10",  # Shri Mahavir Jayanti
    "2025-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-08-15",  # Independence Day
    "2025-08-27",  # Ganesh Chaturthi
    "2025-10-02",  # Mahatma Gandhi Jayanti / Dussehra
    "2025-10-20",  # Diwali Laxmi Pujan (Muhurat trading only)
    "2025-10-21",  # Diwali Balipratipada
    "2025-11-05",  # Guru Nanak Jayanti
    "2025-12-25",  # Christmas
    # 2026
    "2026-01-26",  # Republic Day
    "2026-02-26",  # Mahashivratri (approx)
    "2026-03-20",  # Holi (approx)
    "2026-04-02",  # Eid (approx)
    "2026-04-03",  # Good Friday (approx)
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-10-02",  # Gandhi Jayanti
    "2026-11-05",  # Diwali / Guru Nanak (approx)
    "2026-12-25",  # Christmas
}

# ---------------------------------------------------------------------------
# Rate limiting for Anthropic API (avoid overloading during seed of 97 stocks)
# ---------------------------------------------------------------------------
API_RETRY_ATTEMPTS = int(os.environ.get("PA_API_RETRIES", "3"))
API_RETRY_DELAY_S  = float(os.environ.get("PA_API_RETRY_DELAY", "5.0"))
# Delay between symbols during seed to respect rate limits
INTER_SYMBOL_DELAY_S = float(os.environ.get("PA_INTER_SYMBOL_DELAY", "2.0"))

# ---------------------------------------------------------------------------
# Batch API settings (used when --batch flag is passed)
# PA_BATCH_POLL_INTERVAL — seconds between status checks while batch is running
# PA_BATCH_MAX_WAIT      — total seconds before TimeoutError is raised
# ---------------------------------------------------------------------------
BATCH_POLL_INTERVAL_S = int(os.environ.get("PA_BATCH_POLL_INTERVAL", "30"))
BATCH_MAX_WAIT_S      = int(os.environ.get("PA_BATCH_MAX_WAIT",      "7200"))  # 2 hours

# ---------------------------------------------------------------------------
# Setup Taxonomy Parameters — injected into prompts.py at import time
# Override any value via PA_<KEY> environment variable (uppercase key).
# ---------------------------------------------------------------------------
SETUP_PARAMS = {
    "min_sr_tests":              int(os.environ.get("PA_MIN_SR_TESTS",              "2")),
    "pullback_vol_max":          float(os.environ.get("PA_PULLBACK_VOL_MAX",        "0.8")),
    "rejection_wick_ratio":      float(os.environ.get("PA_REJECTION_WICK_RATIO",    "0.5")),
    "trap_lookback_bars":        int(os.environ.get("PA_TRAP_LOOKBACK_BARS",        "3")),
    "breakout_lookback_bars":    int(os.environ.get("PA_BREAKOUT_LOOKBACK_BARS",    "5")),
    "breakout_vol_min":          float(os.environ.get("PA_BREAKOUT_VOL_MIN",        "1.5")),
    "entry_buffer_pct":          float(os.environ.get("PA_ENTRY_BUFFER_PCT",        "0.25")),
    "value_area_vol_min":        float(os.environ.get("PA_VALUE_AREA_VOL_MIN",      "1.0")),
    "value_area_stop_buffer_pct": float(os.environ.get("PA_VALUE_AREA_STOP_BUFFER_PCT", "0.5")),
    "ma_proximity_pct":          float(os.environ.get("PA_MA_PROXIMITY_PCT",        "0.5")),
    "ma_stop_buffer_pct":        float(os.environ.get("PA_MA_STOP_BUFFER_PCT",      "0.5")),
    "consolidation_min_bars":    int(os.environ.get("PA_CONSOLIDATION_MIN_BARS",    "4")),
    "consolidation_max_bars":    int(os.environ.get("PA_CONSOLIDATION_MAX_BARS",    "8")),
    "consolidation_max_range_pct": float(os.environ.get("PA_CONSOLIDATION_MAX_RANGE_PCT", "2.5")),
    "consolidation_vol_max":     float(os.environ.get("PA_CONSOLIDATION_VOL_MAX",   "0.7")),
    "consolidation_close_range_pct": float(os.environ.get("PA_CONSOLIDATION_CLOSE_RANGE_PCT", "25")),
    "momentum_vol_min":          float(os.environ.get("PA_MOMENTUM_VOL_MIN",        "2.0")),
    "momentum_close_beyond_pct": float(os.environ.get("PA_MOMENTUM_CLOSE_BEYOND_PCT", "0.5")),
    "momentum_close_range_pct":  float(os.environ.get("PA_MOMENTUM_CLOSE_RANGE_PCT", "25")),
    "max_gap_pct":               float(os.environ.get("PA_MAX_GAP_PCT",             "1.75")),
    "momentum_target_min_pct":   float(os.environ.get("PA_MOMENTUM_TARGET_MIN_PCT", "3.0")),
    "momentum_target_max_pct":   float(os.environ.get("PA_MOMENTUM_TARGET_MAX_PCT", "8.0")),
    "rs_weekly_threshold_pct":   float(os.environ.get("PA_RS_WEEKLY_PCT",           "1.0")),
    "rs_daily_threshold_pct":    float(os.environ.get("PA_RS_DAILY_PCT",            "0.5")),
    "rs_max_from_extreme_pct":   float(os.environ.get("PA_RS_MAX_FROM_EXTREME_PCT", "5.0")),
    "rs_min_beta":               float(os.environ.get("PA_RS_MIN_BETA",             "0.8")),
    "rs_clear_space_pct":        float(os.environ.get("PA_RS_CLEAR_SPACE_PCT",      "2.0")),
    "poc_dead_zone_pct":         float(os.environ.get("PA_POC_DEAD_ZONE_PCT",       "0.5")),
    "min_setup_vol":             float(os.environ.get("PA_MIN_SETUP_VOL",           "1.0")),
    "min_rr_ratio":              float(os.environ.get("PA_MIN_RR_RATIO",              "1")),
    "block_deal_vol_multiple":   float(os.environ.get("PA_BLOCK_DEAL_VOL_MULT",     "3.0")),
    "block_deal_max_price_chg_pct": float(os.environ.get("PA_BLOCK_DEAL_MAX_CHG_PCT", "1.0")),
    "sr_zone_tolerance_pct":     float(os.environ.get("PA_SR_ZONE_TOLERANCE_PCT",   "0.5")),
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("PA_LOG_LEVEL", "INFO")
