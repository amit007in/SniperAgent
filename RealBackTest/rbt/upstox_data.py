"""
Upstox historical data layer with a resumable SQLite cache.

Endpoints used (all official, documented):
  V3 candles      GET /v3/historical-candle/{key}/{unit}/{interval}/{to}/{from}
                  limits: minutes 1-15 -> 1 month/request; >15m & hours -> 1
                  quarter; days -> 1 decade; weeks/months -> unlimited.
                  1-min data available from Jan 2022.
  Expiries        GET /v2/expired-instruments/expiries?instrument_key=
  Expired chain   GET /v2/expired-instruments/option/contract?instrument_key=&expiry_date=
  Expired candles GET /v2/expired-instruments/historical-candle/{ekey}/{interval}/{to}/{from}
                  (ekey = NSE_FO|token|DD-MM-YYYY, intervals incl 1minute, OI
                  in column 7)
  Live chain      GET /v2/option/contract (for an end-window expiry that has
                  not expired yet — its candles come from V3 instead)

NOTE: the expired-instruments family may require an Upstox Plus subscription
(error UDAPI1149). The fetcher detects this, marks the symbol's options plane
unavailable, and the backtest degrades to structure-only for that symbol —
identical to the live engine's chain-fetch-failure path.

Everything lands in data/marketdata.db; every chunk is logged in fetch_log so
an interrupted fetch resumes where it stopped.
"""
import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

from . import config as C

BASE_URL = "https://api.upstox.com"


# ------------------------------------------------------------------ client --
class PlusPlanRequired(RuntimeError):
    pass


class UpstoxClient:
    def __init__(self, token):
        # tolerate accidental quotes/whitespace from `export TOKEN='...'`
        token = (token or "").strip().strip("'\"").strip()
        if not token:
            raise RuntimeError(
                "UPSTOX_ACCESS_TOKEN is not set in this shell. Run\n"
                "    export UPSTOX_ACCESS_TOKEN='<fresh token>'\n"
                "in the SAME terminal session before `realbacktest.py "
                "fetch` (tokens expire daily).")
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json",
                               "Authorization": f"Bearer {token}"})
        self._last = 0.0

    def get(self, url, params=None):
        attempt = 0           # transient failures (5xx / network)
        waits_429 = 0         # quota waits — patient, separate budget
        while attempt < C.MAX_RETRIES and waits_429 < C.RATE_LIMIT_WAITS:
            gap = 1.0 / C.REQS_PER_SEC - (time.time() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.time()
            try:
                r = self.s.get(url, params=params, timeout=C.TIMEOUT_S)
            except requests.RequestException as e:
                attempt += 1
                if attempt >= C.MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.json()
            body = r.text[:300]
            if "UDAPI1149" in body:             # plan gate, any status code
                raise PlusPlanRequired(
                    "Upstox expired-instruments API requires the Plus plan "
                    f"({url}). Options plane will be skipped.")
            if r.status_code in (401, 403):
                raise RuntimeError(
                    f"Upstox rejected the token (HTTP {r.status_code}) on "
                    f"{url}\nAPI said: {body}\nNote: historical-candle "
                    "endpoints are PUBLIC, so equity bars downloading fine "
                    "does NOT mean the token works. Verify it with:\n  curl "
                    "-H \"Authorization: Bearer $UPSTOX_ACCESS_TOKEN\" "
                    "https://api.upstox.com/v2/user/profile\nSandbox tokens "
                    "do not work against the live API.")
            if r.status_code == 429:
                # 30-min quota: waiting out the window is the only cure.
                waits_429 += 1
                wait = min(300, 45 * waits_429)
                hdr = r.headers.get("Retry-After")
                if hdr and hdr.isdigit():
                    wait = max(wait, int(hdr) + 1)
                print(f"  [RATE] 429 — waiting {wait}s "
                      f"({waits_429}/{C.RATE_LIMIT_WAITS})")
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                attempt += 1
                time.sleep(min(60, 2 ** (attempt + 1)))
                continue
            if r.status_code in (400, 404) and "UDAPI" in body:
                return None                     # e.g. no data for that range
            raise RuntimeError(f"HTTP {r.status_code} on {url}: {body}")
        raise RuntimeError(
            f"retries exhausted on {url} — if this was rate limiting, "
            "simply re-run fetch later; it resumes from this chunk.")


# ------------------------------------------------------------------- cache --
SCHEMA = """
CREATE TABLE IF NOT EXISTS equity_candles (
    symbol TEXT, unit TEXT, interval TEXT, ts INTEGER,
    open REAL, high REAL, low REAL, close REAL, volume REAL, oi REAL,
    PRIMARY KEY (symbol, unit, interval, ts)
);
CREATE TABLE IF NOT EXISTS expiries (
    symbol TEXT, expiry TEXT, source TEXT,
    PRIMARY KEY (symbol, expiry)
);
CREATE TABLE IF NOT EXISTS option_contracts (
    instrument_key TEXT PRIMARY KEY, symbol TEXT, expiry TEXT,
    cp TEXT, strike REAL, lot_size REAL
);
CREATE TABLE IF NOT EXISTS option_candles (
    instrument_key TEXT, ts INTEGER,
    open REAL, high REAL, low REAL, close REAL, volume REAL, oi REAL,
    PRIMARY KEY (instrument_key, ts)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    chunk_key TEXT PRIMARY KEY, fetched_at TEXT, n_rows INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE INDEX IF NOT EXISTS idx_opt_sym ON option_contracts (symbol, expiry);
"""


class Cache:
    def __init__(self, path=C.CACHE_DB):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    # -- chunk bookkeeping ----------------------------------------------------
    def done(self, key):
        return self.conn.execute(
            "SELECT 1 FROM fetch_log WHERE chunk_key=?", (key,)).fetchone()

    def mark(self, key, n):
        self.conn.execute(
            "REPLACE INTO fetch_log VALUES (?,?,?)",
            (key, datetime.now().isoformat(), n))
        self.conn.commit()

    def set_meta(self, key, value):
        self.conn.execute("REPLACE INTO meta VALUES (?,?)",
                          (key, json.dumps(value)))
        self.conn.commit()

    def get_meta(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # -- writers ----------------------------------------------------------------
    def put_equity(self, symbol, unit, interval, candles):
        rows = [(symbol, unit, interval, _ts_epoch(c[0]),
                 c[1], c[2], c[3], c[4], c[5], c[6] if len(c) > 6 else 0)
                for c in candles]
        self.conn.executemany(
            "REPLACE INTO equity_candles VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def put_option_candles(self, ikey, candles):
        rows = [(ikey, _ts_epoch(c[0]), c[1], c[2], c[3], c[4], c[5],
                 c[6] if len(c) > 6 else 0) for c in candles]
        self.conn.executemany(
            "REPLACE INTO option_candles VALUES (?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    # -- readers ----------------------------------------------------------------
    def equity_df(self, symbol, unit, interval):
        df = pd.read_sql_query(
            "SELECT ts, open, high, low, close, volume, oi FROM equity_candles"
            " WHERE symbol=? AND unit=? AND interval=? ORDER BY ts",
            self.conn, params=(symbol, unit, interval))
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["ts"], unit="s", utc=True
                                             ).dt.tz_convert("Asia/Kolkata")
        return df

    def contracts(self, symbol):
        return pd.read_sql_query(
            "SELECT * FROM option_contracts WHERE symbol=? ORDER BY expiry,"
            " strike", self.conn, params=(symbol,))

    def option_series(self, ikey):
        return pd.read_sql_query(
            "SELECT ts, close, volume, oi FROM option_candles WHERE"
            " instrument_key=? ORDER BY ts", self.conn, params=(ikey,))

    def expiries(self, symbol):
        return [r[0] for r in self.conn.execute(
            "SELECT expiry FROM expiries WHERE symbol=? ORDER BY expiry",
            (symbol,))]


def _ts_epoch(ts_str):
    return int(pd.Timestamp(ts_str).timestamp())


# ------------------------------------------------------------ chunk helpers --
def month_chunks(start, end, span_days=28):
    """Fixed <=28-day windows: the V3 cap for 1-15 min intervals is one
    month per request and ranges wider than ~30 days can fail or be
    sliced server-side — 28 days clears it with margin."""
    cur = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while cur <= end_d:
        to = min(end_d, cur + timedelta(days=span_days - 1))
        yield cur.isoformat(), to.isoformat()
        cur = to + timedelta(days=1)


def quarter_chunks(start, end):
    cur = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    while cur <= end_d:
        nxt = cur + timedelta(days=88)
        yield cur.isoformat(), min(end_d, nxt).isoformat()
        cur = nxt + timedelta(days=1)


def last_thursdays(start, end):
    """Fallback monthly-expiry calendar (NSE stock options): last Thursday of
    each month in [start, end]. Real holiday shifts are resolved by probing
    the contracts API around the candidate."""
    out = []
    cur = date.fromisoformat(start).replace(day=1)
    end_d = date.fromisoformat(end)
    while cur <= end_d:
        nxt = (cur + timedelta(days=32)).replace(day=1)
        d = nxt - timedelta(days=1)
        while d.weekday() != 3:                  # Thursday
            d -= timedelta(days=1)
        if date.fromisoformat(start) <= d <= end_d:
            out.append(d.isoformat())
        cur = nxt
    return out


# -------------------------------------------------------------- fetch logic --
def fetch_equity(client, cache, ucfg, start, end, log=print):
    """1-min from start; 30-min/daily/weekly with pre-window lookback."""
    sym, key = ucfg["symbol"], ucfg["instrument_key"]
    ekey = quote(key, safe="")
    plans = [
        ("minutes", "1", start, end, month_chunks),
        ("minutes", "30",
         (date.fromisoformat(start)
          - timedelta(days=C.PREFETCH_DAYS["30m"])).isoformat(),
         end, quarter_chunks),
        ("days", "1",
         (date.fromisoformat(start)
          - timedelta(days=C.PREFETCH_DAYS["daily"])).isoformat(),
         end, None),
        ("weeks", "1",
         (date.fromisoformat(start)
          - timedelta(days=C.PREFETCH_DAYS["weekly"])).isoformat(),
         end, None),
    ]
    for unit, interval, fr, to, chunker in plans:
        chunks = list(chunker(fr, to)) if chunker else [(fr, to)]
        for cfr, cto in chunks:
            ck = f"eq|{sym}|{unit}{interval}|{cfr}|{cto}"
            if cache.done(ck):
                continue
            url = (f"{BASE_URL}/v3/historical-candle/{ekey}/{unit}/"
                   f"{interval}/{cto}/{cfr}")
            data = client.get(url)
            candles = (data or {}).get("data", {}).get("candles", []) or []
            n = cache.put_equity(sym, unit, interval, candles)
            cache.mark(ck, n)
            log(f"  [{sym}] {unit}/{interval} {cfr}..{cto}: {n} bars")


def fetch_expiries(client, cache, ucfg, start, end, log=print):
    """Expiry calendar covering the window plus the first expiry beyond it."""
    sym, key = ucfg["symbol"], ucfg["instrument_key"]
    probe_end = (date.fromisoformat(end) + timedelta(days=45)).isoformat()
    got = set(cache.expiries(sym))
    try:
        data = client.get(f"{BASE_URL}/v2/expired-instruments/expiries",
                          params={"instrument_key": key})
        api_exp = [e for e in ((data or {}).get("data") or [])
                   if start <= e <= probe_end]
    except PlusPlanRequired:
        raise
    except Exception as e:
        log(f"  [{sym}] expiries endpoint failed ({e}); using calendar "
            "fallback")
        api_exp = []
    candidates = sorted(set(api_exp) | set(last_thursdays(start, probe_end)))
    for exp in candidates:
        if exp in got:
            continue
        cache.conn.execute("REPLACE INTO expiries VALUES (?,?,?)",
                           (sym, exp, "api" if exp in api_exp else "calendar"))
    cache.conn.commit()
    return cache.expiries(sym)


def fetch_contracts(client, cache, ucfg, expiry, today=None, log=print):
    """Contract list for one expiry — expired endpoint for past expiries,
    live endpoint for a still-running near month. Cached."""
    sym, key = ucfg["symbol"], ucfg["instrument_key"]
    today = today or date.today().isoformat()
    ck = f"oc|{sym}|{expiry}"
    if cache.done(ck):
        return cache.conn.execute(
            "SELECT COUNT(*) FROM option_contracts WHERE symbol=? AND"
            " expiry=?", (sym, expiry)).fetchone()[0]
    if expiry < today:
        data = client.get(
            f"{BASE_URL}/v2/expired-instruments/option/contract",
            params={"instrument_key": key, "expiry_date": expiry})
    else:
        data = client.get(f"{BASE_URL}/v2/option/contract",
                          params={"instrument_key": key,
                                  "expiry_date": expiry})
    rows = (data or {}).get("data") or []
    for r in rows:
        cache.conn.execute(
            "REPLACE INTO option_contracts VALUES (?,?,?,?,?,?)",
            (r["instrument_key"], sym, r.get("expiry", expiry),
             r.get("instrument_type"), float(r.get("strike_price") or 0),
             float(r.get("lot_size") or 0)))
    cache.conn.commit()
    cache.mark(ck, len(rows))
    log(f"  [{sym}] contracts {expiry}: {len(rows)}")
    return len(rows)


def select_strikes(cache, sym, expiry, prev_expiry, start, end):
    """Strikes worth fetching for this expiry's active window: nearest strike
    to each session close, +/- STRIKE_NEIGHBOURS neighbours."""
    daily = cache.equity_df(sym, "days", "1")
    if daily.empty:
        return []
    cons = cache.contracts(sym)
    cons = cons[cons["expiry"] == expiry]
    strikes = sorted(cons["strike"].unique())
    if not strikes:
        return []
    w0 = max(start, prev_expiry or start)
    win = daily[(daily["timestamp"].dt.date.astype(str) > w0)
                & (daily["timestamp"].dt.date.astype(str) <= min(expiry, end))]
    wanted = set()
    arr = pd.Series(strikes)
    for px in win["close"].values:
        idx = int((arr - px).abs().idxmin())
        for j in range(idx - C.STRIKE_NEIGHBOURS,
                       idx + C.STRIKE_NEIGHBOURS + 1):
            if 0 <= j < len(strikes):
                wanted.add(strikes[j])
    return sorted(wanted)


def fetch_option_candles(client, cache, ucfg, expiry, strikes, start, end,
                         today=None, log=print):
    """1-min candles (with OI) for CE+PE of the selected strikes over the
    expiry's active window."""
    sym = ucfg["symbol"]
    today = today or date.today().isoformat()
    cons = cache.contracts(sym)
    cons = cons[(cons["expiry"] == expiry) & cons["strike"].isin(strikes)]
    win_to = min(expiry, end)
    n_tot = 0
    for _, r in cons.iterrows():
        ikey = r["instrument_key"]
        for cfr, cto in month_chunks(start, win_to):
            ck = f"optc|{ikey}|{cfr}|{cto}"
            if cache.done(ck):
                continue
            if expiry < today:
                url = (f"{BASE_URL}/v2/expired-instruments/historical-candle/"
                       f"{quote(ikey, safe='')}/1minute/{cto}/{cfr}")
            else:           # live contract: V3 path, key has no expiry suffix
                url = (f"{BASE_URL}/v3/historical-candle/"
                       f"{quote(ikey, safe='')}/minutes/1/{cto}/{cfr}")
            data = client.get(url)
            candles = (data or {}).get("data", {}).get("candles", []) or []
            n = cache.put_option_candles(ikey, candles)
            cache.mark(ck, n)
            n_tot += n
    log(f"  [{sym}] option candles {expiry}: +{n_tot} bars "
        f"({len(cons)} contracts)")
    return n_tot


def preflight_token(client, log=print):
    """Validate the token against an AUTHENTICATED endpoint before fetching
    anything (historical candles are public and prove nothing)."""
    data = client.get(f"{BASE_URL}/v2/user/profile")
    u = (data or {}).get("data", {})
    log(f"[AUTH] token OK — user {u.get('user_name', '?')} "
        f"({u.get('user_id', '?')}), products {u.get('products')}")


def fetch_all(token, start, end, symbols=None, log=print,
              include_options=True):
    """Orchestrate the full fetch. Resumable; safe to re-run.
    include_options=False stages an equity-only pull (fast) — re-run later
    without the flag to add the options plane incrementally."""
    client = UpstoxClient(token)
    preflight_token(client, log)
    cache = Cache()
    uni = [u for u in C.UNIVERSE
           if symbols is None or u["symbol"] in symbols]
    options_ok = dict(cache.get_meta("options_ok", {}))
    for ucfg in uni:
        sym = ucfg["symbol"]
        log(f"[FETCH] {sym}: equity candles")
        fetch_equity(client, cache, ucfg, start, end, log)
        if not ucfg["has_options"] or not include_options:
            options_ok.setdefault(sym, False)
            continue
        try:
            log(f"[FETCH] {sym}: options plane")
            expiries = fetch_expiries(client, cache, ucfg, start, end, log)
            prev = None
            for exp in expiries:
                n = fetch_contracts(client, cache, ucfg, exp, log=log)
                if n > 0:
                    strikes = select_strikes(cache, sym, exp, prev, start,
                                             end)
                    win_from = max(start, prev or start)
                    fetch_option_candles(client, cache, ucfg, exp, strikes,
                                         win_from, end, log=log)
                prev = exp
            options_ok[sym] = True
        except PlusPlanRequired as e:
            log(f"  [{sym}] !! {e}")
            options_ok[sym] = False
    cache.set_meta("options_ok", options_ok)
    cache.set_meta("window", {"start": start, "end": end})
    log(f"[FETCH] done. options plane: {options_ok}")
    return options_ok


# ------------------------------------------------------------------- audit --
def audit(log=print):
    """Coverage + quality summary of the cache; returns dict for the report."""
    cache = Cache()
    out = {"symbols": {}, "window": cache.get_meta("window"),
           "options_ok": cache.get_meta("options_ok", {})}
    for ucfg in C.UNIVERSE:
        sym = ucfg["symbol"]
        s = {}
        for unit, iv in (("minutes", "1"), ("minutes", "30"), ("days", "1"),
                         ("weeks", "1")):
            df = cache.equity_df(sym, unit, iv)
            if df.empty:
                s[f"{unit}/{iv}"] = {"bars": 0}
                continue
            days = df["timestamp"].dt.date.nunique()
            s[f"{unit}/{iv}"] = {
                "bars": len(df), "sessions": int(days),
                "first": str(df['timestamp'].iloc[0]),
                "last": str(df['timestamp'].iloc[-1]),
                "bars_per_session": round(len(df) / max(1, days), 1)}
        cons = cache.contracts(sym)
        nbars = cache.conn.execute(
            "SELECT COUNT(*) FROM option_candles WHERE instrument_key IN "
            "(SELECT instrument_key FROM option_contracts WHERE symbol=?)",
            (sym,)).fetchone()[0]
        s["options"] = {"contracts": len(cons),
                        "expiries": len(cache.expiries(sym)),
                        "option_bars": int(nbars)}
        out["symbols"][sym] = s
        log(f"[AUDIT] {sym}: " + json.dumps(s, default=str))
    return out
