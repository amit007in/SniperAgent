#!/usr/bin/env python3
"""
PriceActionAgent — Cold Start / Seed Script
============================================
One-time initialization for all NSE 100 symbols.

Algorithm
---------
For each symbol:
  1. Load N months of daily, weekly, and 4H candles from marketdata.db
  2. Split daily candles into weekly chunks (SEED_CHUNK_DAYS per chunk)
  3. Progressive reconciliation: call Claude for each chunk, accumulating narrative
  4. Final synthesis: call Claude with full accumulated narrative + weekly context
     → produces the "gist" + structured trade decision for next trading day
  5. Persist narrative + anchor metrics + trade decision to price_action.db

Usage
-----
  # Seed all 97 symbols (takes ~30-90 min depending on model)
  python seed_cold_start.py

  # Seed specific symbols only
  python seed_cold_start.py --symbols RELIANCE,HDFCBANK,INFY

  # Dry run (no API calls, just data loading)
  python seed_cold_start.py --dry-run

  # Resume (skip already-seeded symbols)
  python seed_cold_start.py --resume

  # Seed with specific date window
  python seed_cold_start.py --end-date 2025-04-30

Environment
-----------
  ANTHROPIC_API_KEY   — required
  PA_MODEL            — override model (default: claude-sonnet-4-6)
  PA_SEED_MONTHS      — months of history (default: 6)
  PA_SEED_CHUNK_DAYS  — days per chunk (default: 5)
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

# Add parent dir to path so config.py resolves
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    ANTHROPIC_MODEL,
    API_RETRY_ATTEMPTS,
    API_RETRY_DELAY_S,
    INTER_SYMBOL_DELAY_S,
    NSE100_SYMBOLS,
    SEED_CHUNK_DAYS,
    SEED_MAX_TOKENS,
    SEED_MONTHS,
    SEED_TEMPERATURE,
    SEED_USE_EXTENDED_THINKING,
    SEED_THINKING_BUDGET,
    LOG_DIR,
)
from batch_api import submit_batch, collect_results
from data_loader import (
    candles_to_csv,
    compute_anchor_metrics,
    get_rolling_context,
    get_symbol_data_window,
)
from nse_calendar import next_trading_day
from pa_store import PAStore
from prompts import (
    build_anchor_block,
    seed_chunk_prompt,
    seed_final_synthesis_prompt,
    SEED_SYSTEM_PROMPT,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "seed_cold_start.log"),
    ],
)
log = logging.getLogger("seed")


# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------
_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY from env
    return _client


# ---------------------------------------------------------------------------
# Core: call Claude with retry
# ---------------------------------------------------------------------------

def call_claude(
    system: str,
    messages: list[dict],
    max_tokens: int = SEED_MAX_TOKENS,
    temperature: float = SEED_TEMPERATURE,
    use_thinking: bool = False,
    thinking_budget: int = SEED_THINKING_BUDGET,
) -> str:
    """Call Claude API with retry on transient errors. Returns text response."""
    client = get_client()

    kwargs: dict = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }

    if use_thinking:
        # Extended thinking: temperature must be 1.0 for thinking models
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": thinking_budget,
        }
        kwargs["temperature"] = 1.0
        # Increase max_tokens to accommodate thinking + output
        kwargs["max_tokens"] = thinking_budget + max_tokens
    else:
        kwargs["temperature"] = temperature

    for attempt in range(1, API_RETRY_ATTEMPTS + 1):
        try:
            response = client.messages.create(**kwargs)
            # Extract text (skip thinking blocks)
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""
        except anthropic.RateLimitError as e:
            wait = API_RETRY_DELAY_S * attempt * 3
            log.warning("Rate limit hit (attempt %d/%d) — waiting %.0fs: %s",
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


# ---------------------------------------------------------------------------
# Core: progressive reconciliation
# ---------------------------------------------------------------------------

def progressive_reconcile(
    symbol: str,
    daily_candles: list,
    h4_candles: list,
    weekly_candles: list,
    anchor_block: str,
    dry_run: bool = False,
) -> tuple[str, list[str]]:
    """
    Split daily candles into weekly chunks. For each chunk:
      1. Back-propagate — validate all prior calls against the new chunk's data
      2. Reconcile — correct invalidated claims and extend the story
    Returns (accumulated_narrative, list_of_chunk_labels).
    """
    chunks = []
    for i in range(0, len(daily_candles), SEED_CHUNK_DAYS):
        chunks.append(daily_candles[i : i + SEED_CHUNK_DAYS])

    total = len(chunks)
    log.info("%s: %d total daily candles → %d chunks of ~%d days",
             symbol, len(daily_candles), total, SEED_CHUNK_DAYS)

    # Build a date→4H map for fast chunk-level 4H extraction
    h4_by_date: dict[str, list] = {}
    for bar in h4_candles:
        day = bar.date[:10]
        h4_by_date.setdefault(day, []).append(bar)

    accumulated_narrative: str | None = None
    chunk_labels: list[str] = []
    conversation_messages: list[dict] = []

    for chunk_idx, chunk in enumerate(chunks):
        is_first = chunk_idx == 0
        import datetime as _dt
        chunk_start_date = _dt.date.fromisoformat(chunk[0].date)
        chunk_end_str    = chunk[-1].date
        chunk_label = f"{chunk[0].date} to {chunk_end_str}"
        chunk_labels.append(chunk_label)

        # 4H candles for this chunk
        chunk_dates = {c.date for c in chunk}
        chunk_h4 = [b for day in sorted(chunk_dates) for b in h4_by_date.get(day, [])]
        h4_csv    = candles_to_csv(chunk_h4) if chunk_h4 else ""
        daily_csv = candles_to_csv(chunk)

        # Rolling OHLCV context for back-propagation (raw data, not just narrative)
        prior_daily_ctx, prior_weekly_ctx, prior_4h_ctx = get_rolling_context(
            symbol=symbol,
            chunk_start=chunk_start_date,
            all_daily=daily_candles,
            all_weekly=weekly_candles,
            all_4h=h4_candles,
        )
        prior_daily_csv  = candles_to_csv(prior_daily_ctx)  if prior_daily_ctx  else ""
        prior_weekly_csv = candles_to_csv(prior_weekly_ctx) if prior_weekly_ctx else ""
        prior_4h_csv     = candles_to_csv(prior_4h_ctx)     if prior_4h_ctx     else ""

        user_msg = seed_chunk_prompt(
            symbol=symbol,
            chunk_label=chunk_label,
            daily_csv=daily_csv,
            h4_csv=h4_csv,
            prior_narrative=accumulated_narrative,
            anchor_block=anchor_block,
            is_first_chunk=is_first,
            chunk_index=chunk_idx,
            total_chunks=total,
            prior_daily_ctx=prior_daily_csv,
            prior_weekly_ctx=prior_weekly_csv,
            prior_4h_ctx=prior_4h_csv,
        )

        log.info("%s [chunk %d/%d: %s]: back-propagating + reconciling...",
                 symbol, chunk_idx + 1, total, chunk_label)

        if dry_run:
            accumulated_narrative = (
                f"[DRY RUN] Reconciled narrative through chunk {chunk_idx+1}/{total} ({chunk_label})"
            )
            log.info("%s: DRY RUN — skipping API call", symbol)
            continue

        # Multi-turn conversation: full history gives Claude the rolling context
        # but the prior_narrative field in the prompt is the explicit anchor
        conversation_messages.append({"role": "user", "content": user_msg})

        response_text = call_claude(
            system=SEED_SYSTEM_PROMPT,
            messages=conversation_messages,
            use_thinking=False,   # extended thinking reserved for final synthesis
        )

        conversation_messages.append({"role": "assistant", "content": response_text})

        # Extract RECONCILED_NARRATIVE section to pass as prior_narrative next chunk
        accumulated_narrative = _extract_narrative_section(response_text)
        log.debug("%s chunk %d/%d: reconciled narrative %d chars",
                  symbol, chunk_idx + 1, total, len(accumulated_narrative))

    return accumulated_narrative or "", chunk_labels


def _extract_narrative_section(response_text: str) -> str:
    """
    Extract the RECONCILED_NARRATIVE (or fallback NARRATIVE) section from
    Claude's structured chunk output. Passes the full corrected story as
    prior_narrative into the next chunk's back-propagation review.
    Falls back to full response if no section header is found.
    """
    # Section terminators — stop extraction when we hit the next structural heading
    _STOP_HEADERS = {
        "TREND_STATUS:", "GAP_LOG:", "ACTIVE_SR_LEVELS:", "KEY_LEVELS:",
        "VOLUME_CHARACTER:", "PHASE:", "BACK_PROPAGATION_REVIEW:",
    }

    # Try RECONCILED_NARRATIVE first (chunk format), then plain NARRATIVE
    for header in ("RECONCILED_NARRATIVE:", "NARRATIVE:"):
        lines = response_text.split("\n")
        in_narrative = False
        narrative_lines: list[str] = []

        for line in lines:
            stripped = line.strip().upper()

            if stripped.startswith(header.upper()):
                in_narrative = True
                rest = line.split(":", 1)[1].strip() if ":" in line else ""
                if rest:
                    narrative_lines.append(rest)
                continue

            if in_narrative:
                if any(stripped.startswith(h) for h in _STOP_HEADERS):
                    break
                narrative_lines.append(line)

        if narrative_lines:
            return "\n".join(narrative_lines).strip()

    # Fallback: full response is still useful context for next chunk
    return response_text.strip()


# ---------------------------------------------------------------------------
# Core: final synthesis
# ---------------------------------------------------------------------------

def final_synthesis(
    symbol: str,
    seed_start: str,
    seed_end: str,
    next_td: str,
    accumulated_narrative: str,
    weekly_candles: list,
    daily_candles: list,
    h4_candles: list,
    anchor_block: str,
    anchor_metrics_dict: dict,
    chunk_labels: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Three-pass final reconciliation:
      Pass 1 — end-to-end consistency audit (back-propagation over full story)
      Pass 2 — authoritative corrected narrative
      Pass 3 — immediately executable trade decision
    Returns parsed JSON dict.
    """
    weekly_csv = candles_to_csv(weekly_candles)
    # Last 60 daily bars for 1D raw audit evidence
    daily_csv = candles_to_csv(daily_candles[-60:]) if daily_candles else ""
    # Last 20 trading days of 4H bars (~40 bars) for 4H raw audit evidence
    h4_csv = candles_to_csv(h4_candles[-(20 * 2):]) if h4_candles else ""

    user_msg = seed_final_synthesis_prompt(
        symbol=symbol,
        seed_start=seed_start,
        seed_end=seed_end,
        next_trading_date=next_td,
        accumulated_narrative=accumulated_narrative,
        weekly_csv=weekly_csv,
        anchor_block=anchor_block,
        anchor_metrics=anchor_metrics_dict,
        all_chunk_labels=chunk_labels,
        daily_csv=daily_csv,
        h4_csv=h4_csv,
    )

    if dry_run:
        log.info("%s: DRY RUN — skipping final synthesis API call", symbol)
        return {
            "final_narrative": f"[DRY RUN] Final narrative for {symbol}",
            "trend_status": "SIDEWAYS",
            "key_support_levels": [],
            "key_resistance_levels": [],
            "volume_character": "NEUTRAL",
            "market_phase": "CONSOLIDATION",
            "trade_decision": {
                "direction": "WAIT",
                "entry_price": None,
                "entry_condition": "dry run",
                "stop_loss": None,
                "target_1": None,
                "target_2": None,
                "risk_reward_ratio": None,
                "confidence": "LOW",
                "confidence_reason": "dry run",
                "rationale": "dry run",
            },
            "data_integrity_check": "DRY RUN",
        }

    log.info("%s: calling Claude for FINAL SYNTHESIS (thinking=%s)...",
             symbol, SEED_USE_EXTENDED_THINKING)

    response_text = call_claude(
        system=SEED_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        max_tokens=SEED_MAX_TOKENS,
        use_thinking=SEED_USE_EXTENDED_THINKING,
        thinking_budget=SEED_THINKING_BUDGET,
    )

    return _parse_json_response(response_text, symbol, "final_synthesis")


def _parse_json_response(text: str, symbol: str, stage: str) -> dict:
    """
    Parse JSON from Claude response. Handles cases where Claude wraps JSON
    in markdown fences (it shouldn't, but be defensive).
    """
    # Strip markdown fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Drop first and last fence lines
        cleaned = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        log.error("%s [%s]: JSON parse error: %s\nRaw response:\n%s",
                  symbol, stage, e, text[:500])
        # Return a minimal fallback structure
        return {
            "final_narrative": text,
            "trend_status": "SIDEWAYS",
            "key_support_levels": [],
            "key_resistance_levels": [],
            "volume_character": "NEUTRAL",
            "market_phase": "CONSOLIDATION",
            "trade_decision": {
                "direction": "WAIT",
                "entry_price": None,
                "entry_condition": "parse error — see raw_json",
                "stop_loss": None,
                "target_1": None,
                "target_2": None,
                "risk_reward_ratio": None,
                "confidence": "LOW",
                "confidence_reason": "JSON parse error",
                "rationale": f"JSON parse error in {stage}",
            },
            "data_integrity_check": f"PARSE_ERROR — {str(e)[:100]}",
        }


# ---------------------------------------------------------------------------
# Per-symbol orchestration
# ---------------------------------------------------------------------------

def seed_symbol(
    symbol: str,
    store: PAStore,
    end_date: datetime.date,
    dry_run: bool,
    resume: bool,
) -> bool:
    """
    Run the full seed pipeline for a single symbol.
    Returns True on success, False on failure.
    """
    # Skip if already seeded and resume mode
    if resume and store.has_narrative(symbol):
        log.info("%s: SKIPPED (already seeded, --resume mode)", symbol)
        return True

    log.info("=" * 60)
    log.info("SEEDING: %s  (end_date=%s)", symbol, end_date)
    log.info("=" * 60)

    try:
        # --- 1. Load all candles ---
        daily, weekly, h4, anchors, actual_start, actual_end = get_symbol_data_window(
            symbol=symbol,
            seed_months=SEED_MONTHS,
            end_date=end_date,
        )

        if len(daily) < 20:
            log.warning("%s: insufficient data (%d days) — skipping", symbol, len(daily))
            return False

        # --- 2. Build anchor block (passed to every prompt) ---
        anchor_block = build_anchor_block(
            symbol=symbol,
            seed_start=actual_start.isoformat(),
            seed_end=actual_end.isoformat(),
            period_high=anchors.period_high,
            period_high_dt=anchors.period_high_dt,
            period_low=anchors.period_low,
            period_low_dt=anchors.period_low_dt,
            ma_50d=anchors.ma_50d,
            ma_20d=anchors.ma_20d,
            avg_vol_20d=anchors.avg_vol_20d,
            last_close=anchors.last_close,
            last_date=anchors.last_date,
        )

        anchor_metrics_dict = {
            "last_close": anchors.last_close,
            "last_date": anchors.last_date,
            "ma_50d": anchors.ma_50d,
            "ma_20d": anchors.ma_20d,
            "avg_vol_20d": anchors.avg_vol_20d,
        }

        # --- 3. Progressive reconciliation with back-propagation ---
        accumulated_narrative, chunk_labels = progressive_reconcile(
            symbol=symbol,
            daily_candles=daily,
            h4_candles=h4,
            weekly_candles=weekly,
            anchor_block=anchor_block,
            dry_run=dry_run,
        )

        # --- 4. Next trading date ---
        next_td = next_trading_day(actual_end).isoformat()

        # --- 5. Final reconciliation (3-pass: audit → corrected narrative → trade decision) ---
        synthesis = final_synthesis(
            symbol=symbol,
            seed_start=actual_start.isoformat(),
            seed_end=actual_end.isoformat(),
            next_td=next_td,
            accumulated_narrative=accumulated_narrative,
            weekly_candles=weekly,
            daily_candles=daily,    # last 60 bars used inside for 1D raw evidence
            h4_candles=h4,          # last 40 4H bars used inside for 4H raw evidence
            anchor_block=anchor_block,
            anchor_metrics_dict=anchor_metrics_dict,
            chunk_labels=chunk_labels,
            dry_run=dry_run,
        )

        # final_narrative is now a dict with 1w_view/1d_view/4h_view/synthesis layers
        raw_narrative = synthesis.get("final_narrative", accumulated_narrative)
        if isinstance(raw_narrative, dict):
            final_narrative = json.dumps(raw_narrative, ensure_ascii=False)
        else:
            final_narrative = str(raw_narrative)
        td = synthesis.get("trade_decision", {})

        # --- 6. Persist to DB ---
        store.upsert_anchor(
            symbol=symbol,
            period_high=anchors.period_high,
            period_high_dt=anchors.period_high_dt,
            period_low=anchors.period_low,
            period_low_dt=anchors.period_low_dt,
            ma_50d=anchors.ma_50d,
            ma_20d=anchors.ma_20d,
            avg_vol_20d=anchors.avg_vol_20d,
            seed_start=actual_start.isoformat(),
            seed_end=actual_end.isoformat(),
        )

        store.upsert_narrative(
            symbol=symbol,
            last_date=actual_end.isoformat(),
            seed_date=datetime.date.today().isoformat(),
            narrative=final_narrative,
        )

        store.insert_trade_decision(
            symbol=symbol,
            decision_date=next_td,
            direction=td.get("direction", "WAIT"),
            entry_price=td.get("entry_price"),
            stop_loss=td.get("stop_loss"),
            target_1=td.get("target_1"),
            target_2=td.get("target_2"),
            confidence=td.get("confidence", "LOW"),
            rationale=td.get("rationale", ""),
            raw_json=synthesis,
        )

        log.info(
            "%s: SEEDED ✓  trend=%s  decision=%s  confidence=%s  next_date=%s",
            symbol,
            synthesis.get("trend_status", "?"),
            td.get("direction", "?"),
            td.get("confidence", "?"),
            next_td,
        )
        return True

    except Exception as e:
        log.error("%s: FAILED — %s", symbol, e, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Batch seed orchestration
# ---------------------------------------------------------------------------

def run_batch_seed(
    symbols: list[str],
    store: PAStore,
    end_date: datetime.date,
    dry_run: bool,
    resume: bool,
) -> dict[str, list[str]]:
    """
    Batch-mode cold-start seed for all symbols.

    Strategy
    --------
    Because chunk calls are sequential *per symbol* (each response feeds into
    the next chunk's prompt), we parallelise *across* symbols while keeping
    *intra-symbol* ordering intact:

      Round 0 : chunk-0 for all symbols            → 1 batch
      Round 1 : chunk-1 for all still-active syms  → 1 batch
      ...
      Round N : chunk-N for remaining syms          → 1 batch
      Final   : final-synthesis for all syms        → 1 batch

    Total batch round-trips = max_chunks_across_symbols + 1
    (vs. sequential: num_symbols × (max_chunks + 1) API calls)

    Returns results dict: {success: [...], failed: [...], skipped: [...]}
    """
    import datetime as _dt

    results: dict[str, list[str]] = {
        "success": [], "failed": [], "skipped": [],
    }

    # -----------------------------------------------------------------------
    # Phase 1 — filter and load data for all active symbols
    # -----------------------------------------------------------------------
    log.info("[batch-seed] Phase 1: loading data for %d symbols ...", len(symbols))

    # Per-symbol state bag — populated during data load, consumed during rounds
    symbol_state: dict[str, dict] = {}

    for symbol in symbols:
        if resume and store.has_narrative(symbol):
            log.info("%s: SKIPPED (already seeded, --resume)", symbol)
            results["skipped"].append(symbol)
            continue

        try:
            daily, weekly, h4, anchors, actual_start, actual_end = get_symbol_data_window(
                symbol=symbol, seed_months=SEED_MONTHS, end_date=end_date,
            )
            if len(daily) < 20:
                log.warning("%s: insufficient data (%d days) — skipping", symbol, len(daily))
                results["failed"].append(symbol)
                continue

            anchor_block = build_anchor_block(
                symbol=symbol,
                seed_start=actual_start.isoformat(),
                seed_end=actual_end.isoformat(),
                period_high=anchors.period_high,
                period_high_dt=anchors.period_high_dt,
                period_low=anchors.period_low,
                period_low_dt=anchors.period_low_dt,
                ma_50d=anchors.ma_50d,
                ma_20d=anchors.ma_20d,
                avg_vol_20d=anchors.avg_vol_20d,
                last_close=anchors.last_close,
                last_date=anchors.last_date,
            )
            anchor_metrics_dict = {
                "last_close": anchors.last_close,
                "last_date":  anchors.last_date,
                "ma_50d":     anchors.ma_50d,
                "ma_20d":     anchors.ma_20d,
                "avg_vol_20d": anchors.avg_vol_20d,
            }

            # Pre-split into chunks
            chunks = [daily[i: i + SEED_CHUNK_DAYS]
                      for i in range(0, len(daily), SEED_CHUNK_DAYS)]

            # Build date → 4H bar map once per symbol
            h4_by_date: dict[str, list] = {}
            for bar in h4:
                h4_by_date.setdefault(bar.date[:10], []).append(bar)

            symbol_state[symbol] = {
                "daily":               daily,
                "weekly":              weekly,
                "h4":                  h4,
                "anchors":             anchors,
                "anchor_block":        anchor_block,
                "anchor_metrics_dict": anchor_metrics_dict,
                "actual_start":        actual_start,
                "actual_end":          actual_end,
                "chunks":              chunks,
                "h4_by_date":          h4_by_date,
                "conversation_msgs":   [],   # accumulates multi-turn history
                "accumulated_narrative": None,
                "chunk_labels":        [],
                "status":              "active",  # active | chunks_done | done | failed
            }
            log.info("%s: data loaded — %d daily | %d chunks", symbol, len(daily), len(chunks))

        except Exception as e:
            log.error("%s: data load failed — %s", symbol, e, exc_info=True)
            results["failed"].append(symbol)

    if not symbol_state:
        log.info("[batch-seed] No symbols to process after data load.")
        return results

    # -----------------------------------------------------------------------
    # Phase 2 — round-by-round chunk batches
    # -----------------------------------------------------------------------
    max_rounds = max(len(s["chunks"]) for s in symbol_state.values())
    log.info("[batch-seed] Phase 2: %d chunk rounds across %d symbols",
             max_rounds, len(symbol_state))

    for round_idx in range(max_rounds):
        batch_requests: list[dict] = []

        for symbol, state in symbol_state.items():
            if state["status"] != "active":
                continue
            if round_idx >= len(state["chunks"]):
                # This symbol is done with chunks — mark and skip
                state["status"] = "chunks_done"
                continue

            chunk       = state["chunks"][round_idx]
            chunk_start = _dt.date.fromisoformat(chunk[0].date)
            chunk_label = f"{chunk[0].date} to {chunk[-1].date}"
            state["chunk_labels"].append(chunk_label)

            chunk_h4 = [
                bar
                for day in sorted(c.date for c in chunk)
                for bar in state["h4_by_date"].get(day, [])
            ]
            h4_csv    = candles_to_csv(chunk_h4) if chunk_h4 else ""
            daily_csv = candles_to_csv(chunk)

            prior_daily_ctx, prior_weekly_ctx, prior_4h_ctx = get_rolling_context(
                symbol=symbol,
                chunk_start=chunk_start,
                all_daily=state["daily"],
                all_weekly=state["weekly"],
                all_4h=state["h4"],
            )
            prior_daily_csv  = candles_to_csv(prior_daily_ctx)  if prior_daily_ctx  else ""
            prior_weekly_csv = candles_to_csv(prior_weekly_ctx) if prior_weekly_ctx else ""
            prior_4h_csv     = candles_to_csv(prior_4h_ctx)     if prior_4h_ctx     else ""

            user_msg = seed_chunk_prompt(
                symbol=symbol,
                chunk_label=chunk_label,
                daily_csv=daily_csv,
                h4_csv=h4_csv,
                prior_narrative=state["accumulated_narrative"],
                anchor_block=state["anchor_block"],
                is_first_chunk=(round_idx == 0),
                chunk_index=round_idx,
                total_chunks=len(state["chunks"]),
                prior_daily_ctx=prior_daily_csv,
                prior_weekly_ctx=prior_weekly_csv,
                prior_4h_ctx=prior_4h_csv,
            )

            # Include full conversation history so Claude has rolling context
            messages = state["conversation_msgs"] + [{"role": "user", "content": user_msg}]

            cid = f"{symbol}_chunk_{round_idx}"
            batch_requests.append({
                "custom_id":   cid,
                "system":      SEED_SYSTEM_PROMPT,
                "messages":    messages,
                "max_tokens":  SEED_MAX_TOKENS,
                "temperature": SEED_TEMPERATURE,
            })

        if not batch_requests:
            log.info("[batch-seed] Round %d: no active symbols — stopping chunk rounds",
                     round_idx)
            break

        log.info("[batch-seed] Round %d/%d: submitting %d chunk requests ...",
                 round_idx + 1, max_rounds, len(batch_requests))

        if dry_run:
            log.info("[batch-seed] DRY RUN — skipping chunk round %d API call", round_idx)
            for symbol, state in symbol_state.items():
                if state["status"] == "active" and round_idx < len(state["chunks"]):
                    state["accumulated_narrative"] = (
                        f"[DRY RUN] chunk {round_idx + 1}/{len(state['chunks'])}"
                    )
                    # Advance conversation_msgs with placeholder
                    chunk_label = state["chunk_labels"][-1] if state["chunk_labels"] else "?"
                    state["conversation_msgs"].append(
                        {"role": "user",      "content": f"[DRY RUN chunk {round_idx}]"}
                    )
                    state["conversation_msgs"].append(
                        {"role": "assistant", "content": state["accumulated_narrative"]}
                    )
            continue

        batch_id = submit_batch(batch_requests)
        try:
            raw_results = collect_results(batch_id)
        except TimeoutError:
            log.error(
                "[batch-seed] Round %d timed out waiting for batch %s. "
                "Re-run with PA_BATCH_MAX_WAIT to extend, or check Anthropic Console.",
                round_idx, batch_id,
            )
            # Mark all active symbols as failed — can't continue without results
            for sym, st in symbol_state.items():
                if st["status"] == "active":
                    st["status"] = "failed"
                    results["failed"].append(sym)
            break

        # Apply responses back to each symbol's state
        for symbol, state in symbol_state.items():
            if state["status"] != "active":
                continue
            if round_idx >= len(state["chunks"]):
                continue
            cid = f"{symbol}_chunk_{round_idx}"
            response_text = raw_results.get(cid)
            if response_text is None:
                log.error("%s: chunk %d failed in batch — marking failed", symbol, round_idx)
                state["status"] = "failed"
                results["failed"].append(symbol)
                continue

            # Append to conversation (multi-turn continuity)
            state["conversation_msgs"].append(
                {"role": "assistant", "content": response_text}
            )
            state["accumulated_narrative"] = _extract_narrative_section(response_text)
            log.debug("%s chunk %d: narrative %d chars",
                      symbol, round_idx, len(state["accumulated_narrative"]))

        # Mark symbols that just finished their last chunk this round
        for state in symbol_state.values():
            if state["status"] == "active" and round_idx + 1 >= len(state["chunks"]):
                state["status"] = "chunks_done"

    # -----------------------------------------------------------------------
    # Phase 3 — final synthesis batch
    # -----------------------------------------------------------------------
    synthesis_symbols = [
        sym for sym, st in symbol_state.items()
        if st["status"] in ("active", "chunks_done")
    ]
    log.info("[batch-seed] Phase 3: final synthesis for %d symbols ...",
             len(synthesis_symbols))

    synthesis_requests: list[dict] = []

    for symbol in synthesis_symbols:
        state = symbol_state[symbol]
        daily_candles  = state["daily"]
        weekly_candles = state["weekly"]
        h4_candles     = state["h4"]
        anchors        = state["anchors"]
        actual_start   = state["actual_start"]
        actual_end     = state["actual_end"]
        next_td        = next_trading_day(actual_end).isoformat()

        weekly_csv = candles_to_csv(weekly_candles)
        daily_csv  = candles_to_csv(daily_candles[-60:]) if daily_candles else ""
        h4_csv     = candles_to_csv(h4_candles[-(20 * 2):]) if h4_candles else ""

        user_msg = seed_final_synthesis_prompt(
            symbol=symbol,
            seed_start=actual_start.isoformat(),
            seed_end=actual_end.isoformat(),
            next_trading_date=next_td,
            accumulated_narrative=state["accumulated_narrative"] or "",
            weekly_csv=weekly_csv,
            anchor_block=state["anchor_block"],
            anchor_metrics=state["anchor_metrics_dict"],
            all_chunk_labels=state["chunk_labels"],
            daily_csv=daily_csv,
            h4_csv=h4_csv,
        )

        req: dict = {
            "custom_id":  f"{symbol}_synthesis",
            "system":     SEED_SYSTEM_PROMPT,
            "messages":   [{"role": "user", "content": user_msg}],
            "max_tokens": SEED_MAX_TOKENS,
        }
        if SEED_USE_EXTENDED_THINKING:
            req["thinking"] = {
                "type":          "enabled",
                "budget_tokens": SEED_THINKING_BUDGET,
            }
        else:
            req["temperature"] = SEED_TEMPERATURE

        synthesis_requests.append(req)

    if dry_run:
        log.info("[batch-seed] DRY RUN — skipping final synthesis API call")
        for symbol in synthesis_symbols:
            state = symbol_state[symbol]
            actual_end = state["actual_end"]
            next_td    = next_trading_day(actual_end).isoformat()
            anchors    = state["anchors"]
            _persist_seed_result(
                symbol=symbol, store=store,
                anchors=anchors,
                actual_start=state["actual_start"],
                actual_end=actual_end,
                next_td=next_td,
                synthesis={
                    "final_narrative": f"[DRY RUN] {symbol}",
                    "trend_status": "SIDEWAYS",
                    "key_support_levels": [],
                    "key_resistance_levels": [],
                    "volume_character": "NEUTRAL",
                    "market_phase": "CONSOLIDATION",
                    "trade_decision": {
                        "direction": "WAIT", "entry_price": None,
                        "stop_loss": None, "target_1": None, "target_2": None,
                        "risk_reward_ratio": None, "confidence": "LOW",
                        "confidence_reason": "dry run", "rationale": "dry run",
                    },
                    "data_integrity_check": "DRY RUN",
                },
            )
            results["success"].append(symbol)
        return results

    if not synthesis_requests:
        log.warning("[batch-seed] No symbols reached final synthesis.")
        return results

    batch_id = submit_batch(synthesis_requests)
    try:
        raw_results = collect_results(batch_id)
    except TimeoutError:
        log.error(
            "[batch-seed] Final synthesis timed out waiting for batch %s. "
            "Increase PA_BATCH_MAX_WAIT and re-run with --resume to retry failed symbols.",
            batch_id,
        )
        for sym in synthesis_symbols:
            results["failed"].append(sym)
        return results

    # -----------------------------------------------------------------------
    # Phase 4 — persist all results
    # -----------------------------------------------------------------------
    log.info("[batch-seed] Phase 4: persisting results ...")

    for symbol in synthesis_symbols:
        cid           = f"{symbol}_synthesis"
        response_text = raw_results.get(cid)
        state         = symbol_state[symbol]
        actual_end    = state["actual_end"]
        next_td       = next_trading_day(actual_end).isoformat()
        anchors       = state["anchors"]

        if response_text is None:
            log.error("%s: synthesis batch request failed", symbol)
            results["failed"].append(symbol)
            continue

        synthesis = _parse_json_response(response_text, symbol, "batch_final_synthesis")

        try:
            _persist_seed_result(
                symbol=symbol, store=store,
                anchors=anchors,
                actual_start=state["actual_start"],
                actual_end=actual_end,
                next_td=next_td,
                synthesis=synthesis,
            )
            log.info(
                "%s: SEEDED ✓ [batch]  trend=%s  decision=%s  confidence=%s  next_date=%s",
                symbol,
                synthesis.get("trend_status", "?"),
                synthesis.get("trade_decision", {}).get("direction", "?"),
                synthesis.get("trade_decision", {}).get("confidence", "?"),
                next_td,
            )
            results["success"].append(symbol)
        except Exception as e:
            log.error("%s: persist failed — %s", symbol, e, exc_info=True)
            results["failed"].append(symbol)

    return results


def _persist_seed_result(
    symbol: str,
    store: PAStore,
    anchors,
    actual_start: datetime.date,
    actual_end: datetime.date,
    next_td: str,
    synthesis: dict,
) -> None:
    """Write anchor + narrative + trade_decision for one seeded symbol."""
    raw_narrative = synthesis.get("final_narrative", "")
    if isinstance(raw_narrative, dict):
        final_narrative = json.dumps(raw_narrative, ensure_ascii=False)
    else:
        final_narrative = str(raw_narrative)

    td = synthesis.get("trade_decision", {})

    store.upsert_anchor(
        symbol=symbol,
        period_high=anchors.period_high,
        period_high_dt=anchors.period_high_dt,
        period_low=anchors.period_low,
        period_low_dt=anchors.period_low_dt,
        ma_50d=anchors.ma_50d,
        ma_20d=anchors.ma_20d,
        avg_vol_20d=anchors.avg_vol_20d,
        seed_start=actual_start.isoformat(),
        seed_end=actual_end.isoformat(),
    )
    store.upsert_narrative(
        symbol=symbol,
        last_date=actual_end.isoformat(),
        seed_date=datetime.date.today().isoformat(),
        narrative=final_narrative,
    )
    store.insert_trade_decision(
        symbol=symbol,
        decision_date=next_td,
        direction=td.get("direction", "WAIT"),
        entry_price=td.get("entry_price"),
        stop_loss=td.get("stop_loss"),
        target_1=td.get("target_1"),
        target_2=td.get("target_2"),
        confidence=td.get("confidence", "LOW"),
        rationale=td.get("rationale", ""),
        raw_json=synthesis,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="PriceActionAgent cold-start seed for NSE 100 symbols"
    )
    p.add_argument(
        "--symbols", type=str, default="",
        help="Comma-separated symbols to seed (default: all 97 in config)",
    )
    p.add_argument(
        "--end-date", type=str, default="",
        help="Seed window end date YYYY-MM-DD (default: last trading day)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Load data but skip Anthropic API calls",
    )
    p.add_argument(
        "--resume", action="store_true",
        help="Skip symbols already present in the DB",
    )
    p.add_argument(
        "--list-symbols", action="store_true",
        help="Print all configured symbols and exit",
    )
    p.add_argument(
        "--batch", action="store_true",
        help=(
            "Use Anthropic Message Batches API: parallelise all symbols across rounds "
            "instead of processing each symbol sequentially. "
            "Round-trip count = max_chunks_per_symbol + 1 (final synthesis) "
            "vs. sequential: num_symbols × (max_chunks + 1) API calls."
        ),
    )
    return p.parse_args()


def main():
    args = parse_args()

    if args.list_symbols:
        print(f"\n{len(NSE100_SYMBOLS)} configured symbols:")
        print(", ".join(NSE100_SYMBOLS))
        return

    # Resolve end date
    if args.end_date:
        end_date = datetime.date.fromisoformat(args.end_date)
    else:
        from nse_calendar import last_trading_day
        end_date = last_trading_day()
    log.info("Seed window end date: %s", end_date)

    # Resolve symbols
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = NSE100_SYMBOLS
    log.info("Symbols to seed: %d", len(symbols))

    if args.dry_run:
        log.info("DRY RUN mode — no Anthropic API calls will be made")

    if args.batch:
        log.info("BATCH mode — symbols will be parallelised via Anthropic Message Batches API")

    # Run seed
    with PAStore() as store:
        if args.batch:
            results = run_batch_seed(
                symbols=symbols,
                store=store,
                end_date=end_date,
                dry_run=args.dry_run,
                resume=args.resume,
            )
        else:
            results = {"success": [], "failed": [], "skipped": []}

            for i, symbol in enumerate(symbols, 1):
                log.info("\n[%d/%d] Processing %s ...", i, len(symbols), symbol)

                ok = seed_symbol(
                    symbol=symbol,
                    store=store,
                    end_date=end_date,
                    dry_run=args.dry_run,
                    resume=args.resume,
                )

                if ok:
                    results["success"].append(symbol)
                else:
                    results["failed"].append(symbol)

                # Inter-symbol delay to respect API rate limits (skip last)
                if i < len(symbols) and not args.dry_run:
                    time.sleep(INTER_SYMBOL_DELAY_S)

        # Final summary
        summary = store.summary()

    log.info("\n" + "=" * 60)
    log.info("SEED COMPLETE")
    log.info("  Succeeded: %d", len(results["success"]))
    log.info("  Failed:    %d — %s", len(results["failed"]), results["failed"])
    log.info("  DB Summary: %s", summary)
    log.info("=" * 60)

    if results["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
