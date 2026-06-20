"""
PriceActionAgent — Market Data Loader
=======================================
Reads OHLCV candles from Data.nosync/marketdata.db and prepares
them for the price-action analysis pipeline.

Key responsibilities
--------------------
* Load daily, weekly, and 30-min (4H-aggregated) candles for a symbol
* Compute anchor metrics: period high/low, 50-day MA, 20-day MA, avg volume
* Format candle data as compact, token-efficient CSV strings for LLM prompts
* Anti-hallucination: returns only data that exists in the DB — never fills gaps
"""
import datetime
import logging
import sqlite3
from pathlib import Path
from typing import NamedTuple

from config import MARKET_DATA_DB, ROLLING_4H_DAYS, ROLLING_1D_DAYS, ROLLING_1W_WEEKS

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------

class Candle(NamedTuple):
    date: str     # "YYYY-MM-DD" or "YYYY-MM-DD HH:MM" for intraday
    open: float
    high: float
    low: float
    close: float
    volume: float


class AnchorMetrics(NamedTuple):
    period_high: float
    period_high_dt: str
    period_low: float
    period_low_dt: str
    ma_50d: float
    ma_20d: float
    avg_vol_20d: float
    last_close: float
    last_date: str


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    db = Path(MARKET_DATA_DB)
    if not db.exists():
        raise FileNotFoundError(
            f"Market data DB not found: {db}\n"
            "Run: python RealBackTest/realbacktest.py fetch"
        )
    c = sqlite3.connect(str(db), check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _ts_range(start: datetime.date, end: datetime.date) -> tuple[int, int]:
    """Convert date range to Unix timestamp range (NSE open = 09:15 IST = 03:45 UTC)."""
    # Start of the start day in IST (UTC = IST - 5:30)
    ts_start = int(datetime.datetime(
        start.year, start.month, start.day, 3, 45, 0,
        tzinfo=datetime.timezone.utc
    ).timestamp())
    # End of end day in IST (16:00 IST = 10:30 UTC)
    ts_end = int(datetime.datetime(
        end.year, end.month, end.day, 10, 30, 0,
        tzinfo=datetime.timezone.utc
    ).timestamp())
    return ts_start, ts_end


def _fmt_date(ts: int, intraday: bool = False) -> str:
    """Format unix timestamp as date or datetime string."""
    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc) \
                         .astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    if intraday:
        return dt.strftime("%Y-%m-%d %H:%M")
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_daily_candles(
    symbol: str,
    start: datetime.date,
    end: datetime.date,
) -> list[Candle]:
    """Load daily OHLCV candles for symbol in [start, end]."""
    ts_start, ts_end = _ts_range(start, end)
    with _conn() as c:
        rows = c.execute(
            """SELECT ts, open, high, low, close, volume
               FROM equity_candles
               WHERE symbol=? AND unit='days' AND interval='1'
                 AND ts >= ? AND ts <= ?
               ORDER BY ts""",
            (symbol, ts_start, ts_end),
        ).fetchall()
    candles = [
        Candle(
            date=_fmt_date(r["ts"]),
            open=r["open"], high=r["high"],
            low=r["low"],   close=r["close"],
            volume=r["volume"],
        )
        for r in rows
    ]
    log.debug("load_daily_candles %s %s→%s: %d candles", symbol, start, end, len(candles))
    return candles


def load_weekly_candles(
    symbol: str,
    start: datetime.date,
    end: datetime.date,
) -> list[Candle]:
    """Load weekly OHLCV candles for symbol in [start, end]."""
    ts_start, ts_end = _ts_range(start, end)
    with _conn() as c:
        rows = c.execute(
            """SELECT ts, open, high, low, close, volume
               FROM equity_candles
               WHERE symbol=? AND unit='weeks' AND interval='1'
                 AND ts >= ? AND ts <= ?
               ORDER BY ts""",
            (symbol, ts_start, ts_end),
        ).fetchall()
    return [
        Candle(
            date=_fmt_date(r["ts"]),
            open=r["open"], high=r["high"],
            low=r["low"],   close=r["close"],
            volume=r["volume"],
        )
        for r in rows
    ]


def load_30min_candles(
    symbol: str,
    start: datetime.date,
    end: datetime.date,
) -> list[Candle]:
    """Load 30-minute OHLCV candles for symbol in [start, end]."""
    ts_start, ts_end = _ts_range(start, end)
    with _conn() as c:
        rows = c.execute(
            """SELECT ts, open, high, low, close, volume
               FROM equity_candles
               WHERE symbol=? AND unit='minutes' AND interval='30'
                 AND ts >= ? AND ts <= ?
               ORDER BY ts""",
            (symbol, ts_start, ts_end),
        ).fetchall()
    return [
        Candle(
            date=_fmt_date(r["ts"], intraday=True),
            open=r["open"], high=r["high"],
            low=r["low"],   close=r["close"],
            volume=r["volume"],
        )
        for r in rows
    ]


def aggregate_4h_candles(candles_30m: list[Candle]) -> list[Candle]:
    """
    Aggregate 30-min candles into 4-hour candles.
    NSE session: 09:15–15:30 IST  →  8 × 30-min bars per day
    4H bars: 09:15–13:15, 13:15–15:30 (partial last bar is kept)
    """
    from itertools import groupby

    def _bucket(date_str: str) -> str:
        """Map "YYYY-MM-DD HH:MM" to 4H bucket label."""
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        hour = dt.hour + dt.minute / 60
        if hour < 13.25:
            bucket = "09:15"
        else:
            bucket = "13:15"
        return f"{dt.strftime('%Y-%m-%d')} {bucket}"

    grouped: dict[str, list[Candle]] = {}
    for c in candles_30m:
        key = _bucket(c.date)
        grouped.setdefault(key, []).append(c)

    result = []
    for key in sorted(grouped):
        bars = grouped[key]
        result.append(Candle(
            date=key,
            open=bars[0].open,
            high=max(b.high for b in bars),
            low=min(b.low for b in bars),
            close=bars[-1].close,
            volume=sum(b.volume for b in bars),
        ))
    return result


def compute_anchor_metrics(daily_candles: list[Candle]) -> AnchorMetrics:
    """
    Compute hard anchor metrics from daily candles.
    These are passed as immutable facts to Claude to prevent hallucination.
    """
    if not daily_candles:
        raise ValueError("Cannot compute anchor metrics: no daily candles provided.")

    closes = [c.close for c in daily_candles]
    highs  = [c.high  for c in daily_candles]
    lows   = [c.low   for c in daily_candles]
    vols   = [c.volume for c in daily_candles]

    # Period high / low (by close)
    ph_idx = highs.index(max(highs))
    pl_idx = lows.index(min(lows))

    # Moving averages (trailing)
    def sma(n: int) -> float:
        if len(closes) >= n:
            return round(sum(closes[-n:]) / n, 2)
        return round(sum(closes) / len(closes), 2)

    avg_vol = round(sum(vols[-20:]) / min(20, len(vols)), 0)

    return AnchorMetrics(
        period_high=round(max(highs), 2),
        period_high_dt=daily_candles[ph_idx].date,
        period_low=round(min(lows), 2),
        period_low_dt=daily_candles[pl_idx].date,
        ma_50d=sma(50),
        ma_20d=sma(20),
        avg_vol_20d=avg_vol,
        last_close=round(closes[-1], 2),
        last_date=daily_candles[-1].date,
    )


def candles_to_csv(candles: list[Candle], max_rows: int | None = None) -> str:
    """
    Convert candles to compact CSV string for LLM prompts.
    Format: Date,O,H,L,C,V  (volume in thousands, 2dp prices)
    """
    rows = candles[-max_rows:] if max_rows else candles
    lines = ["Date,Open,High,Low,Close,Vol(K)"]
    for c in rows:
        lines.append(
            f"{c.date},{c.open:.2f},{c.high:.2f},{c.low:.2f},{c.close:.2f},"
            f"{int(c.volume/1000)}"
        )
    return "\n".join(lines)


def get_symbol_data_window(
    symbol: str,
    seed_months: int = 4,
    end_date: datetime.date | None = None,
    rolling_days: int | None = None,
) -> tuple[
    list[Candle],   # daily
    list[Candle],   # weekly
    list[Candle],   # 4H (aggregated from 30min)
    AnchorMetrics,
    datetime.date,  # actual start date
    datetime.date,  # actual end date
]:
    """
    Load all timeframe data for a symbol over the rolling window.
    rolling_days (trading days) takes priority over seed_months if provided.
    Returns daily, weekly, 4H candles + anchor metrics + actual date range.
    """
    if end_date is None:
        end_date = datetime.date.today() - datetime.timedelta(days=1)

    if rolling_days is not None:
        # Convert trading days to calendar days with 1.45x buffer (accounts for weekends/holidays)
        calendar_days = int(rolling_days * 1.45)
        start_date = end_date - datetime.timedelta(days=calendar_days)
    else:
        start_date = end_date - datetime.timedelta(days=seed_months * 30)

    daily  = load_daily_candles(symbol, start_date, end_date)
    weekly = load_weekly_candles(symbol, start_date, end_date)
    raw_30m = load_30min_candles(symbol, start_date, end_date)
    h4     = aggregate_4h_candles(raw_30m)

    if not daily:
        raise ValueError(f"No daily candles found for {symbol} in {start_date}→{end_date}")

    anchors = compute_anchor_metrics(daily)

    actual_start = datetime.date.fromisoformat(daily[0].date)
    actual_end   = datetime.date.fromisoformat(daily[-1].date)

    log.info(
        "%s: %d daily | %d weekly | %d 4H candles  [%s → %s]",
        symbol, len(daily), len(weekly), len(h4), actual_start, actual_end,
    )
    return daily, weekly, h4, anchors, actual_start, actual_end


def load_latest_day_candles(
    symbol: str,
    target_date: datetime.date,
) -> tuple[list[Candle], Candle | None]:
    """
    Load 30-min candles for a single trading day (for daily update).
    Also returns the daily candle for that date (or None if not yet available).
    """
    intraday = load_30min_candles(symbol, target_date, target_date)

    # Try to get the daily bar for that date
    ts_start, ts_end = _ts_range(target_date, target_date)
    with _conn() as c:
        row = c.execute(
            """SELECT ts, open, high, low, close, volume
               FROM equity_candles
               WHERE symbol=? AND unit='days' AND interval='1'
                 AND ts >= ? AND ts <= ?
               LIMIT 1""",
            (symbol, ts_start, ts_end),
        ).fetchone()
    daily_bar = None
    if row:
        daily_bar = Candle(
            date=_fmt_date(row["ts"]),
            open=row["open"], high=row["high"],
            low=row["low"],   close=row["close"],
            volume=row["volume"],
        )
    return intraday, daily_bar


def get_recent_daily_candles(
    symbol: str,
    end_date: datetime.date,
    n_days: int = 60,
) -> list[Candle]:
    """Load the last n_days of daily candles up to end_date (for rolling anchors)."""
    start = end_date - datetime.timedelta(days=n_days * 2)  # generous buffer
    candles = load_daily_candles(symbol, start, end_date)
    return candles[-n_days:]


# ---------------------------------------------------------------------------
# Rolling context loaders — raw OHLCV windows for back-propagation
# These ensure each chunk and daily update has actual data (not just narrative
# text) for the lookback periods needed to validate prior claims.
#
# Window sizes come from config.py (env-overridable):
#   PA_ROLLING_4H_DAYS  — default 20 trading days (~1 month of 4H swing structure)
#   PA_ROLLING_1D_DAYS  — default 60 daily bars   (~3 months, HH/HL + S/R)
#   PA_ROLLING_1W_WEEKS — default 26 weekly bars  (full 6-month seed window)
# ---------------------------------------------------------------------------


def get_rolling_context(
    symbol: str,
    chunk_start: datetime.date,
    all_daily: list[Candle],
    all_weekly: list[Candle],
    all_4h: list[Candle],
) -> tuple[list[Candle], list[Candle], list[Candle]]:
    """
    Return the rolling lookback windows for back-propagation at each chunk boundary.

    Returns:
        prior_daily_ctx  — last ROLLING_1D_DAYS daily candles BEFORE chunk_start
        prior_weekly_ctx — last ROLLING_1W_WEEKS weekly candles BEFORE chunk_start
        prior_4h_ctx     — last ROLLING_4H_DAYS days of 4H bars BEFORE chunk_start

    These are passed alongside each chunk's own candles so Claude has raw OHLCV
    evidence to validate prior claims — not just narrative text.
    """
    cs = chunk_start.isoformat()

    # Daily: last ROLLING_1D_DAYS bars strictly before chunk_start
    prior_daily = [c for c in all_daily if c.date < cs]
    prior_daily_ctx = prior_daily[-ROLLING_1D_DAYS:]

    # Weekly: last ROLLING_1W_WEEKS bars strictly before chunk_start
    prior_weekly = [c for c in all_weekly if c.date < cs]
    prior_weekly_ctx = prior_weekly[-ROLLING_1W_WEEKS:]

    # 4H: last ROLLING_4H_DAYS trading-days' worth of 4H bars before chunk_start
    prior_4h = [c for c in all_4h if c.date[:10] < cs]
    # Each trading day has ~2 4H bars (09:15-13:15 and 13:15-15:30)
    prior_4h_ctx = prior_4h[-(ROLLING_4H_DAYS * 2):]

    return prior_daily_ctx, prior_weekly_ctx, prior_4h_ctx


# Known Nifty 50 symbol variants stored by different Upstox fetch pipelines
_NIFTY_SYMBOL_VARIANTS = [
    "NIFTY 50",
    "Nifty 50",
    "NIFTY50",
    "NSE_INDEX|Nifty 50",
]


def load_nifty_daily(
    start: datetime.date,
    end: datetime.date,
) -> list[Candle]:
    """
    Load Nifty 50 daily candles from the shared marketdata.db.

    Tries multiple known symbol variants — returns the first match.
    Returns an empty list (gracefully) if Nifty data is not in the DB;
    callers treat None nifty_context as "RS not available" and omit it
    from the anchor block rather than failing.
    """
    ts_start, ts_end = _ts_range(start, end)
    try:
        with _conn() as c:
            for sym in _NIFTY_SYMBOL_VARIANTS:
                rows = c.execute(
                    """SELECT ts, open, high, low, close, volume
                       FROM equity_candles
                       WHERE symbol=? AND unit='days' AND interval='1'
                         AND ts >= ? AND ts <= ?
                       ORDER BY ts""",
                    (sym, ts_start, ts_end),
                ).fetchall()
                if rows:
                    log.debug("load_nifty_daily: matched symbol '%s', %d rows", sym, len(rows))
                    return [
                        Candle(
                            date=_fmt_date(r["ts"]),
                            open=r["open"], high=r["high"],
                            low=r["low"],   close=r["close"],
                            volume=r["volume"],
                        )
                        for r in rows
                    ]
    except Exception as e:
        log.warning("load_nifty_daily: could not load Nifty data (non-fatal): %s", e)
    log.debug("load_nifty_daily: Nifty 50 not found in DB for %s→%s — RS skipped", start, end)
    return []


def get_daily_update_context(
    symbol: str,
    session_date: datetime.date,
) -> tuple[list[Candle], list[Candle], list[Candle]]:
    """
    Load rolling context for daily update — raw OHLCV for all three timeframes.

    Returns:
        daily_ctx   — last ROLLING_1D_DAYS daily candles up to (not including) session_date
        weekly_ctx  — last ROLLING_1W_WEEKS weekly candles up to session_date
        h4_ctx      — last ROLLING_4H_DAYS days of 4H candles up to (not including) session_date
    """
    # 1D: last ROLLING_1D_DAYS (60) trading days before session_date
    # Buffer factor 1.5× to account for weekends/holidays in calendar days
    d_start = session_date - datetime.timedelta(days=int(ROLLING_1D_DAYS * 1.5))
    daily_all = load_daily_candles(symbol, d_start, session_date)
    # Exclude today itself — today's bar is passed separately
    daily_ctx = [c for c in daily_all if c.date < session_date.isoformat()]
    daily_ctx = daily_ctx[-ROLLING_1D_DAYS:]

    # 1W: last ROLLING_1W_WEEKS (26) weekly bars up to session_date
    w_start = session_date - datetime.timedelta(weeks=ROLLING_1W_WEEKS + 4)
    weekly_ctx = load_weekly_candles(symbol, w_start, session_date)
    weekly_ctx = weekly_ctx[-ROLLING_1W_WEEKS:]

    # 4H: last ROLLING_4H_DAYS (20) trading days of 30-min bars before session_date
    # Buffer 1.5× calendar days
    h4_start = session_date - datetime.timedelta(days=int(ROLLING_4H_DAYS * 1.5))
    raw_30m = load_30min_candles(symbol, h4_start, session_date)
    # Exclude today's intraday — passed separately
    raw_30m_prior = [c for c in raw_30m if c.date[:10] < session_date.isoformat()]
    h4_ctx = aggregate_4h_candles(raw_30m_prior)
    h4_ctx = h4_ctx[-(ROLLING_4H_DAYS * 2):]   # up to 40 4H bars (~20 trading days)

    return daily_ctx, weekly_ctx, h4_ctx
