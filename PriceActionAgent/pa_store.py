"""
PriceActionAgent — Persistence Layer
======================================
SQLite-backed store for price-action narratives, anchor metrics, and
trade decisions. One DB per agent run; thread-safe via WAL mode.

Schema
------
price_action_narrative
    symbol          TEXT PK
    last_date       TEXT         -- "YYYY-MM-DD" of the last candle processed
    seed_date       TEXT         -- "YYYY-MM-DD" when seed was run
    narrative       TEXT         -- the evolving price-action story
    updated_at      TEXT         -- ISO-8601 UTC

anchor_metrics
    symbol          TEXT PK
    period_high     REAL         -- highest close in seed window
    period_high_dt  TEXT
    period_low      REAL         -- lowest close in seed window
    period_low_dt   TEXT
    ma_50d          REAL         -- 50-day simple moving average (last known)
    ma_20d          REAL         -- 20-day SMA
    avg_vol_20d     REAL         -- 20-day average daily volume
    seed_start      TEXT         -- seed window start date
    seed_end        TEXT         -- seed window end date
    updated_at      TEXT

trade_decisions
    id              INTEGER PK AUTOINCREMENT
    symbol          TEXT
    decision_date   TEXT         -- "YYYY-MM-DD" (for which trading date)
    direction       TEXT         -- BUY | SELL | WAIT
    entry_price     REAL
    stop_loss       REAL
    target_1        REAL
    target_2        REAL
    confidence      TEXT         -- HIGH | MEDIUM | LOW
    rationale       TEXT         -- one-line reasoning
    raw_json        TEXT         -- full Claude JSON response
    created_at      TEXT
"""
import json
import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config import PA_DB


def _resolve_db_path(preferred: Path) -> Path:
    """
    SQLite needs real file locking — synced/cloud folders often lack it.
    Fall back to a temp-dir copy if the preferred path can't host SQLite.
    Override via PA_DB env var.
    """
    env_override = os.environ.get("PA_DB")
    if env_override:
        p = Path(env_override)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        probe = preferred.parent / ".sqlite_probe"
        conn = sqlite3.connect(str(probe))
        conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
        conn.close()
        probe.unlink(missing_ok=True)
        return preferred
    except Exception:
        alt = Path(tempfile.gettempdir()) / "price_action" / "price_action.db"
        alt.parent.mkdir(parents=True, exist_ok=True)
        logging.getLogger(__name__).warning(
            "PA_DB %s cannot host SQLite (synced folder?) — using %s. "
            "Override with PA_DB env var.", preferred, alt
        )
        return alt

log = logging.getLogger(__name__)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


class PAStore:
    """
    Thread-safe (WAL) SQLite store for price-action data.
    Use as a context manager or call open()/close() explicitly.
    """

    def __init__(self, db_path: Path = PA_DB):
        self.db_path = _resolve_db_path(Path(db_path))
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> "PAStore":
        db_path = Path(self.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            timeout=30,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()
        return self

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if not self._conn:
            raise RuntimeError("PAStore not opened. Use 'with PAStore() as s:' or call open().")
        return self._conn

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS price_action_narrative (
                symbol       TEXT PRIMARY KEY,
                last_date    TEXT NOT NULL,
                seed_date    TEXT NOT NULL,
                narrative    TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS anchor_metrics (
                symbol          TEXT PRIMARY KEY,
                period_high     REAL,
                period_high_dt  TEXT,
                period_low      REAL,
                period_low_dt   TEXT,
                ma_50d          REAL,
                ma_20d          REAL,
                avg_vol_20d     REAL,
                seed_start      TEXT,
                seed_end        TEXT,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trade_decisions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol         TEXT NOT NULL,
                decision_date  TEXT NOT NULL,
                direction      TEXT NOT NULL,
                entry_price    REAL,
                stop_loss      REAL,
                target_1       REAL,
                target_2       REAL,
                confidence     TEXT,
                rationale      TEXT,
                raw_json       TEXT,
                created_at     TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_td_symbol_date
                ON trade_decisions(symbol, decision_date);
        """)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Narrative CRUD
    # ------------------------------------------------------------------

    def upsert_narrative(
        self,
        symbol: str,
        last_date: str,
        seed_date: str,
        narrative: str,
    ):
        """Insert or replace the narrative for a symbol."""
        self.conn.execute(
            """INSERT INTO price_action_narrative
                   (symbol, last_date, seed_date, narrative, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                   last_date  = excluded.last_date,
                   narrative  = excluded.narrative,
                   updated_at = excluded.updated_at
            """,
            (symbol, last_date, seed_date, narrative, _now_utc()),
        )
        self.conn.commit()

    def get_narrative(self, symbol: str) -> dict | None:
        """Return narrative row as dict or None."""
        row = self.conn.execute(
            "SELECT * FROM price_action_narrative WHERE symbol = ?", (symbol,)
        ).fetchone()
        return dict(row) if row else None

    def has_narrative(self, symbol: str) -> bool:
        return self.get_narrative(symbol) is not None

    def list_seeded_symbols(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT symbol FROM price_action_narrative ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows]

    # ------------------------------------------------------------------
    # Anchor Metrics CRUD
    # ------------------------------------------------------------------

    def upsert_anchor(
        self,
        symbol: str,
        period_high: float,
        period_high_dt: str,
        period_low: float,
        period_low_dt: str,
        ma_50d: float,
        ma_20d: float,
        avg_vol_20d: float,
        seed_start: str,
        seed_end: str,
    ):
        self.conn.execute(
            """INSERT INTO anchor_metrics
                   (symbol, period_high, period_high_dt, period_low, period_low_dt,
                    ma_50d, ma_20d, avg_vol_20d, seed_start, seed_end, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol) DO UPDATE SET
                   period_high    = excluded.period_high,
                   period_high_dt = excluded.period_high_dt,
                   period_low     = excluded.period_low,
                   period_low_dt  = excluded.period_low_dt,
                   ma_50d         = excluded.ma_50d,
                   ma_20d         = excluded.ma_20d,
                   avg_vol_20d    = excluded.avg_vol_20d,
                   seed_start     = excluded.seed_start,
                   seed_end       = excluded.seed_end,
                   updated_at     = excluded.updated_at
            """,
            (symbol, period_high, period_high_dt, period_low, period_low_dt,
             ma_50d, ma_20d, avg_vol_20d, seed_start, seed_end, _now_utc()),
        )
        self.conn.commit()

    def get_anchor(self, symbol: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM anchor_metrics WHERE symbol = ?", (symbol,)
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Trade Decisions CRUD
    # ------------------------------------------------------------------

    def insert_trade_decision(
        self,
        symbol: str,
        decision_date: str,
        direction: str,
        entry_price: float | None,
        stop_loss: float | None,
        target_1: float | None,
        target_2: float | None,
        confidence: str,
        rationale: str,
        raw_json: dict | str,
    ):
        raw = json.dumps(raw_json) if isinstance(raw_json, dict) else raw_json
        self.conn.execute(
            """INSERT INTO trade_decisions
                   (symbol, decision_date, direction, entry_price, stop_loss,
                    target_1, target_2, confidence, rationale, raw_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, decision_date, direction, entry_price, stop_loss,
             target_1, target_2, confidence, rationale, raw, _now_utc()),
        )
        self.conn.commit()

    def get_latest_decision(self, symbol: str) -> dict | None:
        row = self.conn.execute(
            """SELECT * FROM trade_decisions
               WHERE symbol = ?
               ORDER BY decision_date DESC, id DESC
               LIMIT 1""",
            (symbol,),
        ).fetchone()
        return dict(row) if row else None

    def get_decisions_for_date(self, decision_date: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT * FROM trade_decisions
               WHERE decision_date = ?
               ORDER BY symbol""",
            (decision_date,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Quick health summary of the DB."""
        n_narr = self.conn.execute(
            "SELECT COUNT(*) FROM price_action_narrative"
        ).fetchone()[0]
        n_dec = self.conn.execute(
            "SELECT COUNT(*) FROM trade_decisions"
        ).fetchone()[0]
        last_upd = self.conn.execute(
            "SELECT MAX(updated_at) FROM price_action_narrative"
        ).fetchone()[0]
        return {
            "narratives": n_narr,
            "trade_decisions": n_dec,
            "last_updated": last_upd,
        }
