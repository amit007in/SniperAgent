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
DATA_DIR   = REPO_DIR / "Data"                    # shared data root

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
        return Path(os.environ[env_key])
    if _sqlite_ok(preferred):
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
    "claude-sonnet-4-6"           # balance: precision vs cost
)

# Seed: use extended thinking for deeper multi-timeframe analysis
SEED_USE_EXTENDED_THINKING = os.environ.get(
    "PA_SEED_THINKING", "true"
).lower() == "true"

SEED_THINKING_BUDGET = int(os.environ.get("PA_SEED_THINKING_BUDGET", "8000"))

SEED_MAX_TOKENS    = int(os.environ.get("PA_SEED_MAX_TOKENS",  "4096"))
DAILY_MAX_TOKENS   = int(os.environ.get("PA_DAILY_MAX_TOKENS", "2048"))

# Temperature: tight anchoring to data — keep low
SEED_TEMPERATURE   = float(os.environ.get("PA_SEED_TEMP",  "0.15"))
DAILY_TEMPERATURE  = float(os.environ.get("PA_DAILY_TEMP", "0.10"))

# ---------------------------------------------------------------------------
# Seed Parameters
# ---------------------------------------------------------------------------
# How many months of history to ingest for cold-start
SEED_MONTHS = int(os.environ.get("PA_SEED_MONTHS", "6"))

# Weekly chunk size for progressive reconciliation during seed
# Each chunk = N trading days sent per API call
SEED_CHUNK_DAYS = int(os.environ.get("PA_SEED_CHUNK_DAYS", "5"))  # 1 week

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
ROLLING_1W_WEEKS = int(os.environ.get("PA_ROLLING_1W_WEEKS", "26"))

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
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("PA_LOG_LEVEL", "INFO")
