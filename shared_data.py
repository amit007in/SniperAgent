"""
Canonical paths for the shared marketdata.db used across SniperAgent.

Default: <repo>/Data.nosync/marketdata.db  (local, WAL-safe)

Overrides (first match wins for the DB file):
  MARKET_DATA_DB  — full path to marketdata.db
  RBT_DATA_DIR    — directory containing marketdata.db
  PA_DATA_DIR     — directory containing marketdata.db
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent


def _sqlite_ok(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        p = d / ".sqlite_probe"
        conn = sqlite3.connect(str(p))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
        conn.commit()
        conn.close()
        p.unlink(missing_ok=True)
        for suf in ("-wal", "-shm"):
            (d / (p.name + suf)).unlink(missing_ok=True)
        return True
    except Exception:
        return False


def market_data_dir() -> Path:
    """Directory that hosts marketdata.db."""
    explicit_db = os.environ.get("MARKET_DATA_DB", "").strip()
    if explicit_db:
        return Path(explicit_db).parent
    for env in ("RBT_DATA_DIR", "PA_DATA_DIR"):
        val = os.environ.get(env, "").strip()
        if val:
            return Path(val)
    nosync = REPO / "Data.nosync"
    if _sqlite_ok(nosync):
        return nosync
    fallback = REPO / "Data"
    if _sqlite_ok(fallback):
        print(f"[CONFIG] Data.nosync unavailable — using {fallback} "
              f"(set RBT_DATA_DIR or MARKET_DATA_DB to override)")
        return fallback
    alt = Path(tempfile.gettempdir()) / "sniper_data"
    alt.mkdir(parents=True, exist_ok=True)
    print(f"[CONFIG] cannot host SQLite under Data.nosync or Data — "
          f"using {alt}")
    return alt


def market_data_db() -> Path:
    """Full path to the shared marketdata.db."""
    explicit_db = os.environ.get("MARKET_DATA_DB", "").strip()
    if explicit_db:
        return Path(explicit_db)
    return market_data_dir() / "marketdata.db"
