#!/usr/bin/env python3
"""
PriceActionAgent — Daily EOD Update Job
=========================================
Runs after NSE market close (16:00 IST) to update each symbol's
price-action narrative with the day's session data and generate a
fresh trade decision for the next trading day.

Usage
-----
  # Update all seeded symbols for today's session
  python daily_update.py

  # Update specific symbols
  python daily_update.py --symbols RELIANCE,HDFCBANK

  # Update for a specific past date (for backtesting or catch-up)
  python daily_update.py --date 2025-05-02

  # Dry run — load data but skip API call
  python daily_update.py --dry-run

  # Force update even if already updated today
  python daily_update.py --force

Scheduling (cron — runs Mon-Fri at 16:05 IST)
----------------------------------------------
  5 10 * * 1-5 cd /path/to/SniperAgent && ANTHROPIC_API_KEY=xxx python PriceActionAgent/daily_update.py >> PriceActionAgent/logs/daily_cron.log 2>&1

  Note: 10:35 UTC = 16:05 IST (UTC+5:30)
  The script itself guards against NSE holidays — safe to run via cron daily.

Environment
-----------
  ANTHROPIC_API_KEY   — required
  PA_MODEL            — model override (default: claude-sonnet-4-6)
  PA_SYMBOLS          — comma-separated symbol override
"""
import argparse
import datetime
import json
import logging
import sys
import time
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    ANTHROPIC_MODEL,
    API_RETRY_ATTEMPTS,
    API_RETRY_DELAY_S,
    DAILY_MAX_TOKENS,
    DAILY_TEMPERATURE,
    INTER_SYMBOL_DELAY_S,
    NSE100_SYMBOLS,
    LOG_DIR,
)
from batch_api import submit_batch, collect_results
from data_loader import (
    candles_to_csv,
    compute_anchor_metrics,
    get_daily_update_context,
    get_recent_daily_candles,
    load_latest_day_candles,
)
from nse_calendar import is_trading_day, last_trading_day, next_trading_day
from pa_store import PAStore
from prompts import (
    DAILY_UPDATE_SYSTEM_PROMPT,
    build_anchor_block,
    daily_update_prompt,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "daily_update.log"),
    ],
)
log = logging.getLogger("daily_update")

# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------
_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# ---------------------------------------------------------------------------
# Claude call with retry
# ---------------------------------------------------------------------------

def call_claude(system: str, messages: list[dict]) -> str:
    client = get_client()
    for attempt in range(1, API_RETRY_ATTEMPTS + 1):
        try:
            response = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=DAILY_MAX_TOKENS,
                temperature=DAILY_TEMPERATURE,
                system=system,
                messages=messages,
            )
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""
        except anthropic.RateLimitError as e:
            wait = API_RETRY_DELAY_S * attempt * 3
            log.warning("Rate limit (attempt %d/%d) — waiting %.0fs: %s",
                        attempt, API_RETRY_ATTEMPTS, wait, e)
            if attempt < API_RETRY_ATTEMPTS:
                time.sleep(wait)
        except anthropic.APIStatusError as e:
            log.error("API error (attempt %d/%d): %s", attempt, API_RETRY_ATTEMPTS, e)
            if attempt < API_RETRY_ATTEMPTS:
                time.sleep(API_RETRY_DELAY_S)
        except Exception as e:
            log.error("Unexpected error (attempt %d/%d): %s", attempt, API_RETRY_ATTEMPTS, e)
            if attempt < API_RETRY_ATTEMPTS:
                time.sleep(API_RETRY_DELAY_S)
    raise RuntimeError(f"Claude API failed after {API_RETRY_ATTEMPTS} attempts")


def _parse_json_response(text: str, symbol: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.error("%s: JSON parse error: %s\nRaw:\n%s", symbol, e, text[:400])
        return {
            "full_narrative": text,
            "narrative_update": text[:300],
            "trend_status": "SIDEWAYS",
            "session_character": "NEUTRAL",
            "volume_signal": "AVERAGE",
            "key_support_levels": [],
            "key_resistance_levels": [],
            "trade_decision": {
                "direction": "WAIT",
                "entry_price": None,
                "entry_condition": "parse error",
                "stop_loss": None,
                "target_1": None,
                "target_2": None,
                "risk_reward_ratio": None,
                "confidence": "LOW",
                "confidence_reason": f"JSON parse error: {str(e)[:80]}",
                "rationale": "Could not parse Claude response",
            },
            "data_integrity_check": f"PARSE_ERROR",
        }


# ---------------------------------------------------------------------------
# Per-symbol update
# ---------------------------------------------------------------------------

_RESULT_UPDATED = "updated"
_RESULT_SKIPPED = "skipped"
_RESULT_FAILED  = "failed"


def update_symbol(
    symbol: str,
    store: PAStore,
    session_date: datetime.date,
    next_td: datetime.date,
    dry_run: bool,
    force: bool,
) -> str:
    """
    Update narrative and trade decision for one symbol.
    Returns one of: _RESULT_UPDATED | _RESULT_SKIPPED | _RESULT_FAILED
    """
    # --- Guard: symbol must be seeded ---
    narrative_row = store.get_narrative(symbol)
    if not narrative_row:
        log.warning("%s: not yet seeded — run seed_cold_start.py first", symbol)
        return _RESULT_FAILED

    # --- Guard: already updated for this session date ---
    last_date = narrative_row.get("last_date", "")
    if last_date == session_date.isoformat() and not force:
        log.info("%s: already up-to-date for %s — skipping", symbol, session_date)
        return _RESULT_SKIPPED

    log.info("%s: updating for session %s ...", symbol, session_date)

    try:
        # --- 1. Load today's candles ---
        intraday, daily_bar = load_latest_day_candles(symbol, session_date)

        if not daily_bar and not intraday:
            log.warning("%s: no data found for %s — skipping", symbol, session_date)
            return _RESULT_SKIPPED

        # Compose a "daily bar" CSV whether we got it from DB or derive from intraday
        if daily_bar:
            daily_bar_csv = candles_to_csv([daily_bar])
        elif intraday:
            # Reconstruct daily bar from intraday
            from data_loader import Candle
            synth = Candle(
                date=session_date.isoformat(),
                open=intraday[0].open,
                high=max(b.high for b in intraday),
                low=min(b.low for b in intraday),
                close=intraday[-1].close,
                volume=sum(b.volume for b in intraday),
            )
            daily_bar_csv = candles_to_csv([synth])
            daily_bar = synth
        else:
            daily_bar_csv = "(not available)"

        intraday_csv = candles_to_csv(intraday) if intraday else ""

        # --- 2. Rolling anchor refresh (last 60 trading days) ---
        recent_daily = get_recent_daily_candles(symbol, session_date, n_days=60)
        if len(recent_daily) >= 5:
            updated_anchors = compute_anchor_metrics(recent_daily)
        else:
            # Fall back to stored anchor
            stored_anchor = store.get_anchor(symbol)
            from data_loader import AnchorMetrics
            updated_anchors = AnchorMetrics(
                period_high=stored_anchor["period_high"],
                period_high_dt=stored_anchor["period_high_dt"],
                period_low=stored_anchor["period_low"],
                period_low_dt=stored_anchor["period_low_dt"],
                ma_50d=stored_anchor["ma_50d"],
                ma_20d=stored_anchor["ma_20d"],
                avg_vol_20d=stored_anchor["avg_vol_20d"],
                last_close=daily_bar.close if daily_bar else 0.0,
                last_date=session_date.isoformat(),
            )

        # --- 3. Anchor block (last 60D window) ---
        anchor_block = build_anchor_block(
            symbol=symbol,
            seed_start=(session_date - datetime.timedelta(days=60)).isoformat(),
            seed_end=session_date.isoformat(),
            period_high=updated_anchors.period_high,
            period_high_dt=updated_anchors.period_high_dt,
            period_low=updated_anchors.period_low,
            period_low_dt=updated_anchors.period_low_dt,
            ma_50d=updated_anchors.ma_50d,
            ma_20d=updated_anchors.ma_20d,
            avg_vol_20d=updated_anchors.avg_vol_20d,
            last_close=daily_bar.close if daily_bar else None,
            last_date=session_date.isoformat(),
        )

        # Recent 10-day context CSV (for Claude's rolling trend awareness)
        recent_10 = recent_daily[-10:] if recent_daily else []
        recent_context_csv = candles_to_csv(recent_10) if recent_10 else "(not available)"

        # --- 4. Extract prev_close for gap computation ---
        # prev_close = second-to-last row in recent_10 (last row = today)
        prev_close: float | None = None
        today_open: float | None = None
        if len(recent_10) >= 2:
            prev_close = recent_10[-2].close   # yesterday's close
        if daily_bar:
            today_open = daily_bar.open

        # --- 5. Rolling OHLCV context for back-propagation ---
        try:
            rolling_daily, rolling_weekly, rolling_4h = get_daily_update_context(
                symbol=symbol,
                session_date=session_date,
            )
            rolling_1d_csv = candles_to_csv(rolling_daily)  if rolling_daily  else ""
            rolling_1w_csv = candles_to_csv(rolling_weekly) if rolling_weekly else ""
            rolling_4h_csv = candles_to_csv(rolling_4h)     if rolling_4h     else ""
        except Exception as e:
            log.warning("%s: could not load rolling context (non-fatal): %s", symbol, e)
            rolling_1d_csv = rolling_1w_csv = rolling_4h_csv = ""

        # --- 6. Build prompt ---
        existing_narrative = narrative_row["narrative"]

        user_msg = daily_update_prompt(
            symbol=symbol,
            today_date=session_date.isoformat(),
            next_trading_date=next_td.isoformat(),
            anchor_block=anchor_block,
            existing_narrative=existing_narrative,
            intraday_csv=intraday_csv,
            daily_bar_csv=daily_bar_csv,
            recent_context_csv=recent_context_csv,
            prev_close=prev_close,
            today_open=today_open,
            rolling_1d_ctx=rolling_1d_csv,
            rolling_1w_ctx=rolling_1w_csv,
            rolling_4h_ctx=rolling_4h_csv,
        )

        # --- 7. Call Claude ---
        if dry_run:
            log.info("%s: DRY RUN — skipping API call", symbol)
            return _RESULT_UPDATED

        response_text = call_claude(
            system=DAILY_UPDATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

        result = _parse_json_response(response_text, symbol)

        # --- 8. Validate data integrity ---
        integrity = result.get("data_integrity_check", "")
        if "PASS" not in integrity:
            log.warning("%s: data integrity check did not PASS: %s", symbol, integrity)
            # Still persist — integrity flag is visible for review

        # --- 9. Persist ---
        # full_narrative is now a dict with 1w_view/1d_view/4h_view/synthesis layers.
        # Serialise to JSON string for storage; the next daily update re-reads it as-is.
        raw_narrative = result.get("full_narrative", existing_narrative)
        if isinstance(raw_narrative, dict):
            narrative_str = json.dumps(raw_narrative, ensure_ascii=False)
        else:
            narrative_str = str(raw_narrative)
        td = result.get("trade_decision", {})

        store.upsert_narrative(
            symbol=symbol,
            last_date=session_date.isoformat(),
            seed_date=narrative_row["seed_date"],
            narrative=narrative_str,
        )

        # Update rolling anchor in DB
        store.upsert_anchor(
            symbol=symbol,
            period_high=updated_anchors.period_high,
            period_high_dt=updated_anchors.period_high_dt,
            period_low=updated_anchors.period_low,
            period_low_dt=updated_anchors.period_low_dt,
            ma_50d=updated_anchors.ma_50d,
            ma_20d=updated_anchors.ma_20d,
            avg_vol_20d=updated_anchors.avg_vol_20d,
            seed_start=(session_date - datetime.timedelta(days=60)).isoformat(),
            seed_end=session_date.isoformat(),
        )

        store.insert_trade_decision(
            symbol=symbol,
            decision_date=next_td.isoformat(),
            direction=td.get("direction", "WAIT"),
            entry_price=td.get("entry_price"),
            stop_loss=td.get("stop_loss"),
            target_1=td.get("target_1"),
            target_2=td.get("target_2"),
            confidence=td.get("confidence", "LOW"),
            rationale=td.get("rationale", ""),
            raw_json=result,
        )

        log.info(
            "%s ✓  session=%s  trend=%s  direction=%s  entry=₹%s  sl=₹%s  t1=₹%s  conf=%s",
            symbol,
            session_date,
            result.get("trend_status", "?"),
            td.get("direction", "?"),
            td.get("entry_price", "-"),
            td.get("stop_loss", "-"),
            td.get("target_1", "-"),
            td.get("confidence", "?"),
        )
        return _RESULT_UPDATED

    except Exception as e:
        log.error("%s: FAILED — %s", symbol, e, exc_info=True)
        return _RESULT_FAILED


# ---------------------------------------------------------------------------
# Batch mode helpers
# ---------------------------------------------------------------------------

def _build_symbol_prompt(
    symbol: str,
    store: "PAStore",
    session_date: datetime.date,
    next_td: datetime.date,
    force: bool,
) -> tuple[str, str, object, dict] | None:   # (symbol, user_msg, updated_anchors, narrative_row)
    """
    Build the daily-update user prompt for one symbol.

    Returns (custom_id, user_msg) or None when the symbol should be skipped
    (not seeded, already up-to-date without --force, or no data).

    All data-loading and prompt-assembly logic mirrors update_symbol() exactly
    so that batch and sequential modes produce identical prompts.
    """
    # --- Guard: symbol must be seeded ---
    narrative_row = store.get_narrative(symbol)
    if not narrative_row:
        log.warning("%s: not yet seeded — skipping in batch", symbol)
        return None

    # --- Guard: already updated ---
    last_date = narrative_row.get("last_date", "")
    if last_date == session_date.isoformat() and not force:
        log.info("%s: already up-to-date for %s — skipping in batch", symbol, session_date)
        return None

    try:
        # --- Load today's candles ---
        intraday, daily_bar = load_latest_day_candles(symbol, session_date)

        if not daily_bar and not intraday:
            log.warning("%s: no data for %s — skipping in batch", symbol, session_date)
            return None

        if daily_bar:
            daily_bar_csv = candles_to_csv([daily_bar])
        elif intraday:
            from data_loader import Candle
            synth = Candle(
                date=session_date.isoformat(),
                open=intraday[0].open,
                high=max(b.high for b in intraday),
                low=min(b.low for b in intraday),
                close=intraday[-1].close,
                volume=sum(b.volume for b in intraday),
            )
            daily_bar_csv = candles_to_csv([synth])
            daily_bar = synth
        else:
            daily_bar_csv = "(not available)"

        intraday_csv = candles_to_csv(intraday) if intraday else ""

        # --- Rolling anchor refresh ---
        recent_daily = get_recent_daily_candles(symbol, session_date, n_days=60)
        if len(recent_daily) >= 5:
            updated_anchors = compute_anchor_metrics(recent_daily)
        else:
            stored_anchor = store.get_anchor(symbol)
            from data_loader import AnchorMetrics
            updated_anchors = AnchorMetrics(
                period_high=stored_anchor["period_high"],
                period_high_dt=stored_anchor["period_high_dt"],
                period_low=stored_anchor["period_low"],
                period_low_dt=stored_anchor["period_low_dt"],
                ma_50d=stored_anchor["ma_50d"],
                ma_20d=stored_anchor["ma_20d"],
                avg_vol_20d=stored_anchor["avg_vol_20d"],
                last_close=daily_bar.close if daily_bar else 0.0,
                last_date=session_date.isoformat(),
            )

        # --- Anchor block ---
        anchor_block = build_anchor_block(
            symbol=symbol,
            seed_start=(session_date - datetime.timedelta(days=60)).isoformat(),
            seed_end=session_date.isoformat(),
            period_high=updated_anchors.period_high,
            period_high_dt=updated_anchors.period_high_dt,
            period_low=updated_anchors.period_low,
            period_low_dt=updated_anchors.period_low_dt,
            ma_50d=updated_anchors.ma_50d,
            ma_20d=updated_anchors.ma_20d,
            avg_vol_20d=updated_anchors.avg_vol_20d,
            last_close=daily_bar.close if daily_bar else None,
            last_date=session_date.isoformat(),
        )

        recent_10 = recent_daily[-10:] if recent_daily else []
        recent_context_csv = candles_to_csv(recent_10) if recent_10 else "(not available)"

        prev_close: float | None = None
        today_open: float | None = None
        if len(recent_10) >= 2:
            prev_close = recent_10[-2].close
        if daily_bar:
            today_open = daily_bar.open

        # --- Rolling OHLCV context ---
        try:
            rolling_daily, rolling_weekly, rolling_4h = get_daily_update_context(
                symbol=symbol,
                session_date=session_date,
            )
            rolling_1d_csv = candles_to_csv(rolling_daily)  if rolling_daily  else ""
            rolling_1w_csv = candles_to_csv(rolling_weekly) if rolling_weekly else ""
            rolling_4h_csv = candles_to_csv(rolling_4h)     if rolling_4h     else ""
        except Exception as e:
            log.warning("%s: could not load rolling context (non-fatal): %s", symbol, e)
            rolling_1d_csv = rolling_1w_csv = rolling_4h_csv = ""

        # --- Build prompt ---
        user_msg = daily_update_prompt(
            symbol=symbol,
            today_date=session_date.isoformat(),
            next_trading_date=next_td.isoformat(),
            anchor_block=anchor_block,
            existing_narrative=narrative_row["narrative"],
            intraday_csv=intraday_csv,
            daily_bar_csv=daily_bar_csv,
            recent_context_csv=recent_context_csv,
            prev_close=prev_close,
            today_open=today_open,
            rolling_1d_ctx=rolling_1d_csv,
            rolling_1w_ctx=rolling_1w_csv,
            rolling_4h_ctx=rolling_4h_csv,
        )

        # Stash updated anchors alongside the prompt so _persist_symbol_result
        # can write them without re-loading data.
        # We abuse the return value by returning a 3-tuple; caller unpacks safely.
        return symbol, user_msg, updated_anchors, narrative_row

    except Exception as e:
        log.error("%s: error building prompt — %s", symbol, e, exc_info=True)
        return None


def _persist_symbol_result(
    symbol: str,
    result: dict,
    store: "PAStore",
    session_date: datetime.date,
    next_td: datetime.date,
    narrative_row: dict,
    updated_anchors,
) -> None:
    """Write narrative + anchor + trade_decision to the DB for one symbol."""
    integrity = result.get("data_integrity_check", "")
    if "PASS" not in integrity:
        log.warning("%s: data integrity check did not PASS: %s", symbol, integrity)

    raw_narrative = result.get("full_narrative", narrative_row["narrative"])
    if isinstance(raw_narrative, dict):
        narrative_str = json.dumps(raw_narrative, ensure_ascii=False)
    else:
        narrative_str = str(raw_narrative)

    td = result.get("trade_decision", {})

    store.upsert_narrative(
        symbol=symbol,
        last_date=session_date.isoformat(),
        seed_date=narrative_row["seed_date"],
        narrative=narrative_str,
    )

    store.upsert_anchor(
        symbol=symbol,
        period_high=updated_anchors.period_high,
        period_high_dt=updated_anchors.period_high_dt,
        period_low=updated_anchors.period_low,
        period_low_dt=updated_anchors.period_low_dt,
        ma_50d=updated_anchors.ma_50d,
        ma_20d=updated_anchors.ma_20d,
        avg_vol_20d=updated_anchors.avg_vol_20d,
        seed_start=(session_date - datetime.timedelta(days=60)).isoformat(),
        seed_end=session_date.isoformat(),
    )

    store.insert_trade_decision(
        symbol=symbol,
        decision_date=next_td.isoformat(),
        direction=td.get("direction", "WAIT"),
        entry_price=td.get("entry_price"),
        stop_loss=td.get("stop_loss"),
        target_1=td.get("target_1"),
        target_2=td.get("target_2"),
        confidence=td.get("confidence", "LOW"),
        rationale=td.get("rationale", ""),
        raw_json=result,
    )

    log.info(
        "%s ✓ [batch]  session=%s  trend=%s  direction=%s  entry=₹%s  sl=₹%s  t1=₹%s  conf=%s",
        symbol,
        session_date,
        result.get("trend_status", "?"),
        td.get("direction", "?"),
        td.get("entry_price", "-"),
        td.get("stop_loss", "-"),
        td.get("target_1", "-"),
        td.get("confidence", "?"),
    )


def run_batch_update(
    symbols: list[str],
    store: "PAStore",
    session_date: datetime.date,
    next_td: datetime.date,
    dry_run: bool,
    force: bool,
) -> dict[str, list[str]]:
    """
    Batch-mode daily update: build all prompts → one Anthropic batch → persist.

    Returns results dict: {updated: [...], skipped: [...], failed: [...]}
    """
    results: dict[str, list[str]] = {
        _RESULT_UPDATED: [],
        _RESULT_SKIPPED: [],
        _RESULT_FAILED:  [],
    }

    # --- Phase 1: Build prompts for all symbols ---
    log.info("[batch] Building prompts for %d symbols ...", len(symbols))
    # payload maps custom_id → (symbol, updated_anchors, narrative_row)
    payload: dict[str, tuple] = {}
    batch_requests: list[dict] = []

    for symbol in symbols:
        built = _build_symbol_prompt(symbol, store, session_date, next_td, force)
        if built is None:
            results[_RESULT_SKIPPED].append(symbol)
            continue
        sym_out, user_msg, updated_anchors, narrative_row = built
        cid = f"{symbol}_daily_{session_date.isoformat()}"
        payload[cid] = (symbol, updated_anchors, narrative_row)
        batch_requests.append({
            "custom_id":   cid,
            "system":      DAILY_UPDATE_SYSTEM_PROMPT,
            "messages":    [{"role": "user", "content": user_msg}],
            "max_tokens":  DAILY_MAX_TOKENS,
            "temperature": DAILY_TEMPERATURE,
        })

    if not batch_requests:
        log.info("[batch] No symbols need updating — all skipped.")
        return results

    log.info("[batch] Submitting %d requests ...", len(batch_requests))

    # --- Dry run: skip API ---
    if dry_run:
        log.info("[batch] DRY RUN — skipping Anthropic API call")
        results[_RESULT_UPDATED].extend(p[0] for p in payload.values())
        return results

    # --- Phase 2: Submit batch ---
    batch_id = submit_batch(batch_requests)
    log.info("[batch] Waiting for batch %s ...", batch_id)

    # --- Phase 3: Collect results ---
    try:
        raw_results = collect_results(batch_id)
    except TimeoutError:
        log.error(
            "[batch] Timed out waiting for batch %s. "
            "Increase PA_BATCH_MAX_WAIT and re-run with --force to retry.",
            batch_id,
        )
        results[_RESULT_FAILED].extend(p[0] for p in payload.values())
        return results

    # --- Phase 4: Parse and persist ---
    for cid, (symbol, updated_anchors, narrative_row) in payload.items():
        response_text = raw_results.get(cid)
        if response_text is None:
            log.error("%s: batch request failed or expired", symbol)
            results[_RESULT_FAILED].append(symbol)
            continue

        result = _parse_json_response(response_text, symbol)
        try:
            _persist_symbol_result(
                symbol=symbol,
                result=result,
                store=store,
                session_date=session_date,
                next_td=next_td,
                narrative_row=narrative_row,
                updated_anchors=updated_anchors,
            )
            results[_RESULT_UPDATED].append(symbol)
        except Exception as e:
            log.error("%s: persist failed — %s", symbol, e, exc_info=True)
            results[_RESULT_FAILED].append(symbol)

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="PriceActionAgent daily EOD update — runs after NSE market close"
    )
    p.add_argument(
        "--symbols", type=str, default="",
        help="Comma-separated symbols to update (default: all seeded symbols)",
    )
    p.add_argument(
        "--date", type=str, default="",
        help="Session date YYYY-MM-DD (default: last trading day)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Load data but skip API calls",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Update even if symbol was already updated today",
    )
    p.add_argument(
        "--report", action="store_true",
        help="Print today's trade decisions and exit (no update)",
    )
    p.add_argument(
        "--batch", action="store_true",
        help=(
            "Use Anthropic Message Batches API: submit all symbol prompts as one batch "
            "instead of sequential per-symbol calls. Faster and cheaper for full runs. "
            "Results are processed after the batch completes (async polling)."
        ),
    )
    return p.parse_args()


def _print_report(store: PAStore, decision_date: str):
    decisions = store.get_decisions_for_date(decision_date)
    if not decisions:
        print(f"\nNo trade decisions found for {decision_date}")
        return

    print(f"\n{'='*70}")
    print(f"  TRADE DECISIONS FOR {decision_date}  ({len(decisions)} stocks)")
    print(f"{'='*70}")
    print(f"{'Symbol':<14} {'Dir':<5} {'Entry':>8} {'SL':>8} {'T1':>8} {'T2':>8} {'RR':>5} {'Conf':<8}")
    print("-" * 70)

    for d in decisions:
        raw = json.loads(d["raw_json"]) if d["raw_json"] else {}
        td = raw.get("trade_decision", {})
        rr = td.get("risk_reward_ratio")
        print(
            f"{d['symbol']:<14} {d['direction']:<5} "
            f"{_fmt(d['entry_price']):>8} {_fmt(d['stop_loss']):>8} "
            f"{_fmt(d['target_1']):>8} {_fmt(d['target_2']):>8} "
            f"{_fmt_rr(rr):>5} {d['confidence']:<8}"
        )

    buys  = [d for d in decisions if d["direction"] == "BUY"]
    sells = [d for d in decisions if d["direction"] == "SELL"]
    waits = [d for d in decisions if d["direction"] == "WAIT"]
    print(f"\nSummary: BUY={len(buys)}  SELL={len(sells)}  WAIT={len(waits)}")

    high_conf = [d for d in decisions if d["confidence"] == "HIGH"]
    if high_conf:
        print(f"\nHIGH CONFIDENCE setups ({len(high_conf)}):")
        for d in high_conf:
            print(f"  {d['symbol']:12}  {d['direction']}  entry=₹{_fmt(d['entry_price'])}  "
                  f"rationale: {(d['rationale'] or '')[:80]}")


def _fmt(v) -> str:
    if v is None:
        return "-"
    try:
        return f"₹{float(v):.2f}"
    except Exception:
        return str(v)


def _fmt_rr(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):.1f}"
    except Exception:
        return str(v)


def main():
    args = parse_args()

    # Resolve session date
    if args.date:
        session_date = datetime.date.fromisoformat(args.date)
    else:
        session_date = last_trading_day()

    # Guard: not a trading day
    if not is_trading_day(session_date):
        log.warning("Session date %s is NOT a trading day (weekend/holiday) — exiting", session_date)
        if not args.force:
            sys.exit(0)
        log.warning("--force override: proceeding anyway")

    next_td = next_trading_day(session_date)
    log.info("Session: %s  |  Next trading day: %s", session_date, next_td)

    with PAStore() as store:
        # Report mode
        if args.report:
            _print_report(store, next_td.isoformat())
            return

        # Resolve symbols
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        else:
            symbols = store.list_seeded_symbols()
            if not symbols:
                log.error("No seeded symbols in DB. Run seed_cold_start.py first.")
                sys.exit(1)

        log.info("Updating %d symbols for session %s ...", len(symbols), session_date)

        if args.batch:
            log.info("[batch mode] All symbols will be submitted as one Anthropic batch.")
            results = run_batch_update(
                symbols=symbols,
                store=store,
                session_date=session_date,
                next_td=next_td,
                dry_run=args.dry_run,
                force=args.force,
            )
        else:
            results = {_RESULT_UPDATED: [], _RESULT_SKIPPED: [], _RESULT_FAILED: []}

            for i, symbol in enumerate(symbols, 1):
                result = update_symbol(
                    symbol=symbol,
                    store=store,
                    session_date=session_date,
                    next_td=next_td,
                    dry_run=args.dry_run,
                    force=args.force,
                )
                results[result].append(symbol)

                # Only sleep between actual API calls, not skips
                if result == _RESULT_UPDATED and i < len(symbols) and not args.dry_run:
                    time.sleep(INTER_SYMBOL_DELAY_S)

        # Summary
        n_updated = len(results[_RESULT_UPDATED])
        n_skipped = len(results[_RESULT_SKIPPED])
        n_failed  = len(results[_RESULT_FAILED])
        db_summary = store.summary()
        log.info("\n%s", "=" * 60)
        log.info("DAILY UPDATE COMPLETE — %s", session_date)
        log.info("  Updated: %d  |  Already up-to-date (skipped): %d  |  Failed: %d",
                 n_updated, n_skipped, n_failed)
        if results[_RESULT_FAILED]:
            log.info("  Failed symbols: %s", results[_RESULT_FAILED])
        log.info("  DB: %s", db_summary)

        # Print today's decisions for quick review
        _print_report(store, next_td.isoformat())

    if results[_RESULT_FAILED]:
        sys.exit(1)


if __name__ == "__main__":
    main()
