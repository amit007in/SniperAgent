#!/usr/bin/env python3
"""
PriceActionAgent — Rolling Synthesis Runner
============================================
Single entry point for all price-action analysis.

Runs every evening after market close for every symbol in the watchlist.
No incremental state. Every run is a full rolling-window synthesis:

  1. Load ROLLING_DAYS of daily, weekly, and 4H candles from marketdata.db
  2. Compute VWAP, Volume Profile, Nifty RS (Phase 2 enrichments)
  3. Build anchor block (fixed reference point for all prompts)
  4. Dual-pass synthesis (default PA_DECISION_ENGINE=dual):
     Pass 1 — SmartEngine flags potential BUY signals (no API call)
     Pass 2 — LLM confirms only when Pass 1 is BUY
     Persist BUY to price_action.db only when both engines agree
  5. Legacy single-pass modes: PA_DECISION_ENGINE=smart|llm

Why full re-run instead of incremental:
  - Every decision is grounded in raw OHLCV truth, not prior model output
  - No compounding narrative error across days
  - Regime changes (trend flips, sector rotations) reflected immediately
  - Architecture is simpler — one code path, always the same process

Usage
-----
  # Run all symbols for today (post-market)
  python synthesis.py

  # Run specific symbols
  python synthesis.py --symbols RELIANCE,HDFCBANK

  # Run for a specific date (e.g. for backtesting a past day)
  python synthesis.py --date 2025-05-30

  # Dry run (data load only, no API calls)
  python synthesis.py --dry-run

  # Batch mode (parallelise across symbols via Anthropic Message Batches API)
  python synthesis.py --batch

Environment
-----------
  PA_DECISION_ENGINE       — dual (default) | smart | llm
  PA_EOD_BUY_MIN_GAIN_PCT  — min % gain vs prior close for BUY (SmartEngine + LLM)
  ANTHROPIC_API_KEY        — required for dual/llm Pass 2
  PA_MODEL                 — override model (default: claude-sonnet-4-6)
  PA_ROLLING_DAYS          — trading days of history per run (default: 90)
  PA_SEED_CHUNK_DAYS       — days per reconciliation chunk (default: 15)
  PA_SEED_THINKING_BUDGET  — extended thinking token budget for final synthesis
"""
import argparse
import datetime
import json
import logging
import sys
import time
from pathlib import Path

if sys.version_info < (3, 10):
    _venv_py = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
    sys.exit(
        f"Python 3.10+ required (you have {sys.version.split()[0]}).\n"
        "Activate the project virtualenv:\n"
        f"  source {Path(__file__).resolve().parent.parent / '.venv' / 'bin' / 'activate'}\n"
        "  python PriceActionAgent/synthesis.py ...\n"
        f"Or: {_venv_py} PriceActionAgent/synthesis.py ..."
    )

import anthropic

_PA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PA_DIR.parent
sys.path.insert(0, str(_PA_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config import (
    ANTHROPIC_MODEL,
    DECISION_ENGINE,
    EOD_BUY_MIN_GAIN_PCT,
    cached_system,
    API_RETRY_ATTEMPTS,
    API_RETRY_DELAY_S,
    INTER_SYMBOL_DELAY_S,
    NSE100_SYMBOLS,
    ROLLING_DAYS,
    SEED_CHUNK_DAYS,
    SEED_MAX_TOKENS,
    SEED_TEMPERATURE,
    SEED_USE_EXTENDED_THINKING,
    SEED_THINKING_BUDGET,
    LOG_DIR,
)
from batch_api import submit_batch, collect_results
from analytics import (
    build_vwap_block,
    build_volume_profile_block,
    compute_nifty_context,
)
from data_loader import (
    candles_to_csv,
    compute_anchor_metrics,
    get_symbol_data_window,
    load_nifty_daily,
)
from nse_calendar import next_trading_day
from pa_store import PAStore
from prompts import (
    build_anchor_block,
    full_synthesis_prompt,
    SYSTEM_PROMPT,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "synthesis.log"),
    ],
)
log = logging.getLogger("synthesis")


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
# Claude API call with retry
# ---------------------------------------------------------------------------

def call_claude(
    system: str,
    messages: list[dict],
    max_tokens: int = SEED_MAX_TOKENS,
    temperature: float = SEED_TEMPERATURE,
    use_thinking: bool = False,
    thinking_budget: int = SEED_THINKING_BUDGET,
) -> str:
    """Call Claude API with exponential backoff retry. Returns text response."""
    client = get_client()

    kwargs: dict = {
        "model":      ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system":     cached_system(system) if isinstance(system, str) else system,
        "messages":   messages,
    }

    if use_thinking:
        kwargs["thinking"]    = {"type": "enabled", "budget_tokens": thinking_budget}
        kwargs["temperature"] = 1.0
        kwargs["max_tokens"]  = thinking_budget + max_tokens
    else:
        kwargs["temperature"] = temperature

    for attempt in range(1, API_RETRY_ATTEMPTS + 1):
        try:
            if use_thinking:
                # Streaming is required by the Anthropic SDK for requests that may
                # exceed 10 minutes (extended thinking + large max_tokens).
                with client.messages.stream(**kwargs) as stream:
                    final = stream.get_final_message()
                for block in final.content:
                    if block.type == "text":
                        return block.text
                return ""
            else:
                response = client.messages.create(**kwargs)
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


# ---------------------------------------------------------------------------
# Single-prompt single-call synthesis
# ---------------------------------------------------------------------------

def run_smart_synthesis_path(
    symbol: str,
    window_start: str,
    window_end: str,
    next_td: str,
    weekly_candles: list,
    daily_candles: list,
    h4_candles: list,
    anchor_metrics_dict: dict,
    vwap_block: dict | None,
    volume_profile_block: dict | None,
    nifty_ctx: dict | None,
) -> dict:
    """Deterministic SmartEngine synthesis — same JSON schema as LLM path."""
    from SmartEngine.engine import run_smart_synthesis

    vp_1d = (volume_profile_block or {}).get("20d")
    vwap = (vwap_block or {}).get("session_vwap")
    out = run_smart_synthesis(
        symbol=symbol,
        window_start=window_start,
        window_end=window_end,
        next_trading_date=next_td,
        weekly=weekly_candles,
        daily=daily_candles,
        h4=h4_candles,
        anchor_metrics=anchor_metrics_dict,
        vwap_1d=vwap,
        vwap_4h=vwap,
        volume_profile_1d=vp_1d,
        nifty_context=nifty_ctx,
    )
    return _apply_eod_buy_gate(symbol, out, daily_candles)


def _trade_action(synthesis: dict) -> str:
    return (synthesis.get("trade_decision") or {}).get("action", "NO_TRADE")


def qualifies_eod_buy_gate(daily_candles: list) -> tuple[bool, str]:
    """
    BUY signals (SmartEngine and LLM) require a green EOD bar (close > open)
    and a gain of at least EOD_BUY_MIN_GAIN_PCT vs the prior session close.
    """
    if len(daily_candles) < 2:
        return False, "insufficient daily bars for EOD filter"

    last = daily_candles[-1]
    prev = daily_candles[-2]

    if last.close <= last.open:
        return False, (
            f"red EOD candle (open={last.open:.2f}, close={last.close:.2f})"
        )

    if prev.close <= 0:
        return False, "invalid prior close for EOD filter"

    chg_pct = (last.close - prev.close) / prev.close * 100
    if chg_pct < EOD_BUY_MIN_GAIN_PCT:
        return False, (
            f"EOD gain {chg_pct:.2f}% below {EOD_BUY_MIN_GAIN_PCT:.1f}% minimum "
            f"(prev={prev.close:.2f}, close={last.close:.2f})"
        )

    return True, f"green EOD +{chg_pct:.2f}%"


def qualifies_for_llm_analysis(daily_candles: list) -> tuple[bool, str]:
    """Alias for qualifies_eod_buy_gate (same rule for LLM and SmartEngine BUY)."""
    return qualifies_eod_buy_gate(daily_candles)


def _apply_eod_buy_gate(symbol: str, synthesis: dict, daily_candles: list) -> dict:
    """Veto SmartEngine BUY when the session fails the EOD BUY gate."""
    td = synthesis.get("trade_decision") or {}
    if td.get("action") != "BUY":
        return synthesis

    ok, reason = qualifies_eod_buy_gate(daily_candles)
    if ok:
        return synthesis

    log.info("%s: SmartEngine BUY vetoed — EOD gate: %s", symbol, reason)
    result = dict(synthesis)
    result["trade_decision"] = {
        "action": "NO_TRADE",
        "setup": "NO_TRADE",
        "entry": None,
        "target": None,
        "stop_loss": None,
        "rejection": (
            f"EOD gate: SmartEngine BUY {td.get('setup')} @ {td.get('entry')} "
            f"vetoed — {reason}"
        ),
    }
    result["eod_filter"] = reason
    return result


def _llm_only_filter_rejected(symbol: str, reason: str) -> dict:
    return {
        "final_narrative": f"[LLM skipped] {symbol}: {reason}",
        "trend_status": {"1w": "SIDEWAYS", "1d": "SIDEWAYS", "4h": "SIDEWAYS",
                         "alignment": "CONFLICTED"},
        "trade_decision": {
            "action": "NO_TRADE",
            "setup": "NO_TRADE",
            "entry": None,
            "target": None,
            "stop_loss": None,
            "rejection": f"EOD gate: {reason}",
        },
        "eod_filter": reason,
    }


def _dual_llm_filter_rejected(symbol: str, smart: dict, reason: str) -> dict:
    """SmartEngine BUY but LLM Pass 2 skipped by EOD filter."""
    smart_td = smart.get("trade_decision") or {}
    rejection = (
        f"Dual-pass: SmartEngine BUY {smart_td.get('setup')} @ {smart_td.get('entry')} — "
        f"LLM skipped (EOD gate): {reason}"
    )
    result = dict(smart)
    result["trade_decision"] = {
        "action": "NO_TRADE",
        "setup": "NO_TRADE",
        "entry": None,
        "target": None,
        "stop_loss": None,
        "rejection": rejection,
    }
    result["dual_pass"] = {
        "pass1_engine": "smart",
        "pass1": smart_td,
        "pass2_engine": "llm",
        "pass2": None,
        "agreed": False,
        "skipped_pass2": True,
        "eod_filter": reason,
    }
    return result


def _merge_dual_pass(symbol: str, smart: dict, llm: dict) -> dict:
    """Combine Pass 1 (SmartEngine) + Pass 2 (LLM). BUY only when both agree."""
    smart_td = smart.get("trade_decision") or {}
    llm_td = llm.get("trade_decision") or {}
    dual_meta = {
        "pass1_engine": "smart",
        "pass1": smart_td,
        "pass2_engine": "llm",
        "pass2": llm_td,
    }

    if llm_td.get("action") == "BUY":
        log.info(
            "%s: DUAL PASS AGREED — SmartEngine BUY %s + LLM BUY %s",
            symbol, smart_td.get("setup"), llm_td.get("setup"),
        )
        merged = dict(llm)
        dual_meta["agreed"] = True
        merged["dual_pass"] = dual_meta
        return merged

    log.info(
        "%s: DUAL PASS REJECTED — SmartEngine BUY %s @ %s, LLM %s",
        symbol, smart_td.get("setup"), smart_td.get("entry"), llm_td.get("action"),
    )
    rejection = (
        f"Dual-pass: SmartEngine BUY {smart_td.get('setup')} @ {smart_td.get('entry')} "
        f"rejected by LLM ({llm_td.get('action')}). "
        f"{llm_td.get('rejection') or ''}"
    ).strip()
    base = llm if llm.get("full_narrative") or llm.get("final_narrative") else smart
    merged = dict(base)
    merged["trade_decision"] = {
        "action": "NO_TRADE",
        "setup": "NO_TRADE",
        "entry": None,
        "target": None,
        "stop_loss": None,
        "rejection": rejection,
        "next_plan": llm_td.get("next_plan"),
    }
    dual_meta["agreed"] = False
    merged["dual_pass"] = dual_meta
    return merged


def run_dual_synthesis(
    symbol: str,
    window_start: str,
    window_end: str,
    next_td: str,
    weekly_candles: list,
    daily_candles: list,
    h4_candles: list,
    anchor_block: str,
    anchor_metrics_dict: dict,
    vwap_block: dict | None,
    volume_profile_block: dict | None,
    nifty_ctx: dict | None,
    *,
    dry_run: bool = False,
) -> dict:
    """
    Pass 1: SmartEngine screens for BUY candidates.
    Pass 2: LLM runs only when Pass 1 is BUY.
    Returns synthesis dict; trade_decision is BUY only when both agree.
    """
    smart = run_smart_synthesis_path(
        symbol=symbol,
        window_start=window_start,
        window_end=window_end,
        next_td=next_td,
        weekly_candles=weekly_candles,
        daily_candles=daily_candles,
        h4_candles=h4_candles,
        anchor_metrics_dict=anchor_metrics_dict,
        vwap_block=vwap_block,
        volume_profile_block=volume_profile_block,
        nifty_ctx=nifty_ctx,
    )
    smart_td = smart.get("trade_decision") or {}
    smart_action = smart_td.get("action", "NO_TRADE")

    if smart_action != "BUY":
        log.info(
            "%s: Pass 1 SmartEngine %s — skipping Pass 2 (LLM)",
            symbol, smart_action,
        )
        result = dict(smart)
        result["dual_pass"] = {
            "pass1_engine": "smart",
            "pass1": smart_td,
            "pass2_engine": None,
            "pass2": None,
            "agreed": False,
            "skipped_pass2": True,
        }
        return result

    log.info(
        "%s: Pass 1 SmartEngine BUY %s @ %s — checking LLM EOD filter",
        symbol, smart_td.get("setup"), smart_td.get("entry"),
    )
    ok, filter_reason = qualifies_for_llm_analysis(daily_candles)
    if not ok:
        log.info("%s: Pass 2 (LLM) skipped — EOD filter: %s", symbol, filter_reason)
        return _dual_llm_filter_rejected(symbol, smart, filter_reason)

    log.info("%s: EOD filter passed (%s) — running Pass 2 (LLM)", symbol, filter_reason)
    if dry_run:
        log.info("%s: DRY RUN — Pass 2 skipped; SmartEngine BUY not confirmed", symbol)
        result = dict(smart)
        result["dual_pass"] = {
            "pass1_engine": "smart",
            "pass1": smart_td,
            "pass2_engine": "llm",
            "pass2": None,
            "agreed": False,
            "skipped_pass2": True,
            "rejection": "DRY RUN — LLM confirmation skipped",
        }
        result["trade_decision"] = {
            "action": "NO_TRADE",
            "setup": "NO_TRADE",
            "entry": None,
            "target": None,
            "stop_loss": None,
            "rejection": "DRY RUN — SmartEngine BUY not confirmed by LLM",
        }
        return result

    llm = run_full_synthesis(
        symbol=symbol,
        window_start=window_start,
        window_end=window_end,
        next_td=next_td,
        weekly_candles=weekly_candles,
        daily_candles=daily_candles,
        h4_candles=h4_candles,
        anchor_block=anchor_block,
        anchor_metrics_dict=anchor_metrics_dict,
        dry_run=False,
    )
    return _merge_dual_pass(symbol, smart, llm)


def run_full_synthesis(
    symbol: str,
    window_start: str,
    window_end: str,
    next_td: str,
    weekly_candles: list,
    daily_candles: list,
    h4_candles: list,
    anchor_block: str,
    anchor_metrics_dict: dict,
    dry_run: bool = False,
) -> dict:
    """
    Single API call per symbol: all OHLCV in one prompt, model reasons
    progressively in extended thinking, outputs one final synthesis JSON.

    Replaces the old progressive_reconcile() + run_final_synthesis() two-step.
    """
    if dry_run:
        log.info("%s: DRY RUN — skipping API call", symbol)
        return _dry_run_result(symbol)

    n_chunks = (len(daily_candles) + SEED_CHUNK_DAYS - 1) // SEED_CHUNK_DAYS
    log.info(
        "%s: full synthesis — %d daily bars in %d chunks, thinking=%s",
        symbol, len(daily_candles), n_chunks, SEED_USE_EXTENDED_THINKING,
    )

    user_msg = full_synthesis_prompt(
        symbol=symbol,
        window_start=window_start,
        window_end=window_end,
        next_trading_date=next_td,
        anchor_block=anchor_block,
        anchor_metrics=anchor_metrics_dict,
        weekly_csv=candles_to_csv(weekly_candles),
        daily_candles=daily_candles,
        h4_candles=h4_candles,
        chunk_size=SEED_CHUNK_DAYS,
    )

    response_text = call_claude(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        max_tokens=SEED_MAX_TOKENS,
        use_thinking=SEED_USE_EXTENDED_THINKING,
        thinking_budget=SEED_THINKING_BUDGET,
    )
    return _parse_json(response_text, symbol, "full_synthesis")


def _dry_run_result(symbol: str) -> dict:
    return {
        "final_narrative": f"[DRY RUN] {symbol}",
        "trend_status":    {"1w": "SIDEWAYS", "1d": "SIDEWAYS", "4h": "SIDEWAYS",
                            "alignment": "CONFLICTED"},
        "trade_decision":  {"action": "NO_TRADE", "entry": None,
                            "target": None, "stop_loss": None},
        "data_integrity_check": "DRY RUN",
    }


def _is_complete_synthesis(parsed: dict) -> bool:
    """True only when trade_decision contains a valid action."""
    td = parsed.get("trade_decision")
    if not isinstance(td, dict):
        return False
    return td.get("action") in ("BUY", "SELL", "NO_TRADE")


def _parse_json(text: str, symbol: str, stage: str) -> dict:
    """
    Extract and parse JSON from model response.

    Recovery chain (each step tried only if the previous failed):
      1. Direct json.loads on stripped text
      2. Strip markdown fences, retry json.loads
      3. Slice from first '{' to last '}', retry json.loads
      4. json_repair — fixes truncation, trailing commas, unescaped chars
      5. Regex extraction of trade_decision fields only (last resort)
    """
    def _default(raw: str) -> dict:
        return {
            "final_narrative": raw,
            "trend_status":    {"1w": "SIDEWAYS", "1d": "SIDEWAYS",
                                "4h": "SIDEWAYS", "alignment": "CONFLICTED"},
            "trade_decision":  {"action": "NO_TRADE", "entry": None,
                                "target": None, "stop_loss": None,
                                "next_plan": None},
            "data_integrity_check": "PARSE_ERROR — recovery failed",
        }

    cleaned = text.strip()

    # Step 1 — direct parse
    try:
        parsed = json.loads(cleaned)
        if _is_complete_synthesis(parsed):
            return parsed
        log.warning("%s [%s]: direct parse incomplete (no trade_decision) — continuing recovery",
                    symbol, stage)
    except json.JSONDecodeError:
        pass

    # Step 2 — strip markdown fences
    if "```" in cleaned:
        no_fence = "\n".join(
            l for l in cleaned.split("\n") if not l.strip().startswith("```")
        ).strip()
        try:
            parsed = json.loads(no_fence)
            if _is_complete_synthesis(parsed):
                return parsed
            log.warning("%s [%s]: fenced parse incomplete — continuing recovery",
                        symbol, stage)
            cleaned = no_fence
        except json.JSONDecodeError:
            cleaned = no_fence  # use de-fenced version for subsequent steps

    # Step 3 — slice to outermost braces (if both exist)
    start = cleaned.find("{")
    end   = cleaned.rfind("}")
    candidate = cleaned[start: end + 1] if (start != -1 and end > start) else cleaned[start:] if start != -1 else cleaned
    if start != -1:
        try:
            parsed = json.loads(candidate)
            if _is_complete_synthesis(parsed):
                return parsed
            log.warning("%s [%s]: sliced parse incomplete — trying json_repair",
                        symbol, stage)
        except json.JSONDecodeError as e:
            log.warning("%s [%s]: standard JSON parse failed (%s) — trying json_repair",
                        symbol, stage, e)

    # Step 4 — json_repair (handles truncation, trailing commas,
    #           unescaped ₹/unicode, missing closing brackets)
    if start != -1:
        try:
            from json_repair import repair_json
            repaired = repair_json(candidate, return_objects=True)
            if isinstance(repaired, dict) and repaired:
                if _is_complete_synthesis(repaired):
                    log.info("%s [%s]: json_repair succeeded", symbol, stage)
                    return repaired
                log.warning(
                    "%s [%s]: json_repair fixed JSON but trade_decision is missing "
                    "(response likely truncated) — trying regex fallback",
                    symbol, stage,
                )
        except Exception as re_err:
            log.warning("%s [%s]: json_repair failed: %s", symbol, stage, re_err)

    # Step 5 — last resort: regex-extract trade_decision fields
    log.error("%s [%s]: falling back to regex extraction of trade_decision", symbol, stage)
    import re
    def _find(pattern: str) -> str | None:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else None

    action    = _find(r'"action"\s*:\s*"(BUY|SELL|NO_TRADE)"') or "NO_TRADE"
    entry     = _find(r'"entry"\s*:\s*([\d.]+)')
    target    = _find(r'"target"\s*:\s*([\d.]+)')
    stop_loss = _find(r'"stop_loss"\s*:\s*([\d.]+)')
    next_plan = _find(r'"next_plan"\s*:\s*"([^"]{5,})"')

    result = _default(text)
    result["trade_decision"] = {
        "action":    action,
        "entry":     float(entry)     if entry     else None,
        "target":    float(target)    if target    else None,
        "stop_loss": float(stop_loss) if stop_loss else None,
        "next_plan": next_plan,
    }
    result["data_integrity_check"] = "PARSE_ERROR — regex fallback used; verify trade_decision"
    # Preserve any partial structure json_repair recovered (e.g. claim_registry)
    if start != -1:
        try:
            from json_repair import repair_json
            partial = repair_json(candidate, return_objects=True)
            if isinstance(partial, dict):
                for key in ("claim_registry", "trend_status", "final_narrative", "full_narrative"):
                    if key in partial and partial[key] and key not in result:
                        result[key] = partial[key]
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# Per-symbol orchestration
# ---------------------------------------------------------------------------

def synthesize_symbol(
    symbol: str,
    store: PAStore,
    session_date: datetime.date,
    dry_run: bool,
) -> bool:
    """
    Full rolling synthesis for a single symbol.
    Loads ROLLING_DAYS of history, sends all OHLCV in one prompt,
    model reasons progressively → persists BUY/SELL/NO_TRADE decision.
    Returns True on success.
    """
    log.info("=" * 60)
    log.info("SYNTHESISING: %s  (session_date=%s)", symbol, session_date)
    log.info("=" * 60)

    try:
        # 1. Load rolling window of candles
        daily, weekly, h4, anchors, window_start, window_end = get_symbol_data_window(
            symbol=symbol,
            end_date=session_date,
            rolling_days=ROLLING_DAYS,
        )
        if len(daily) < 20:
            log.warning("%s: insufficient data (%d days) — skipping", symbol, len(daily))
            return False

        # 2. Phase 2 enrichments (computed once, injected into every prompt)
        nifty_daily    = load_nifty_daily(window_start, window_end)
        vwap_4h        = build_vwap_block(daily, [], anchors.last_close)
        volume_profile = build_volume_profile_block(daily)
        nifty_ctx      = compute_nifty_context(daily, nifty_daily) if nifty_daily else None

        # 3. Build anchor block
        anchor_blk = build_anchor_block(
            symbol=symbol,
            seed_start=window_start.isoformat(),
            seed_end=window_end.isoformat(),
            period_high=anchors.period_high,
            period_high_dt=anchors.period_high_dt,
            period_low=anchors.period_low,
            period_low_dt=anchors.period_low_dt,
            ma_50d=anchors.ma_50d,
            ma_20d=anchors.ma_20d,
            avg_vol_20d=anchors.avg_vol_20d,
            last_close=anchors.last_close,
            last_date=anchors.last_date,
            vwap_4h=vwap_4h,
            volume_profile=volume_profile,
            nifty_context=nifty_ctx,
        )
        anchor_metrics_dict = {
            "last_close":  anchors.last_close,
            "last_date":   anchors.last_date,
            "ma_50d":      anchors.ma_50d,
            "ma_20d":      anchors.ma_20d,
            "avg_vol_20d": anchors.avg_vol_20d,
        }

        # 4. Next trading date
        next_td = next_trading_day(window_end).isoformat()

        # 5. Synthesis — dual-pass (default), smart-only, or llm-only
        if DECISION_ENGINE == "dual":
            synthesis = run_dual_synthesis(
                symbol=symbol,
                window_start=window_start.isoformat(),
                window_end=window_end.isoformat(),
                next_td=next_td,
                weekly_candles=weekly,
                daily_candles=daily,
                h4_candles=h4,
                anchor_block=anchor_blk,
                anchor_metrics_dict=anchor_metrics_dict,
                vwap_block=vwap_4h,
                volume_profile_block=volume_profile,
                nifty_ctx=nifty_ctx,
                dry_run=dry_run,
            )
        elif DECISION_ENGINE == "smart":
            synthesis = run_smart_synthesis_path(
                symbol=symbol,
                window_start=window_start.isoformat(),
                window_end=window_end.isoformat(),
                next_td=next_td,
                weekly_candles=weekly,
                daily_candles=daily,
                h4_candles=h4,
                anchor_metrics_dict=anchor_metrics_dict,
                vwap_block=vwap_4h,
                volume_profile_block=volume_profile,
                nifty_ctx=nifty_ctx,
            )
        else:
            ok, filter_reason = qualifies_for_llm_analysis(daily)
            if not ok:
                log.info("%s: LLM skipped — EOD filter: %s", symbol, filter_reason)
                synthesis = _llm_only_filter_rejected(symbol, filter_reason)
            elif dry_run:
                synthesis = _dry_run_result(symbol)
            else:
                synthesis = run_full_synthesis(
                    symbol=symbol,
                    window_start=window_start.isoformat(),
                    window_end=window_end.isoformat(),
                    next_td=next_td,
                    weekly_candles=weekly,
                    daily_candles=daily,
                    h4_candles=h4,
                    anchor_block=anchor_blk,
                    anchor_metrics_dict=anchor_metrics_dict,
                    dry_run=False,
                )

        # 6. Persist results
        _persist(symbol=symbol, store=store, anchors=anchors,
                 window_start=window_start, window_end=window_end,
                 next_td=next_td, synthesis=synthesis)

        td = synthesis.get("trade_decision") or {}
        dual = synthesis.get("dual_pass") or {}
        extra = ""
        if dual:
            extra = (
                f"  dual_agreed={dual.get('agreed')}"
                f"  pass1={_trade_action({'trade_decision': dual.get('pass1')})}"
            )
        log.info(
            "%s: DONE ✓  action=%s  entry=%s  target=%s  stop=%s  next=%s%s",
            symbol,
            td.get("action") or "NO_TRADE",
            td.get("entry"), td.get("target"), td.get("stop_loss"),
            next_td,
            extra,
        )
        return True

    except Exception as e:
        log.error("%s: FAILED — %s", symbol, e, exc_info=True)
        return False


def _persist(
    symbol: str,
    store: PAStore,
    anchors,
    window_start: datetime.date,
    window_end: datetime.date,
    next_td: str,
    synthesis: dict,
) -> None:
    """Write anchor metrics + narrative + trade_decision to DB."""
    raw_narrative = synthesis.get("full_narrative") or synthesis.get("final_narrative", "")
    narrative_str = (
        json.dumps(raw_narrative, ensure_ascii=False)
        if isinstance(raw_narrative, dict) else str(raw_narrative)
    )

    td        = synthesis.get("trade_decision") or {}
    action    = td.get("action", "NO_TRADE")
    setup     = td.get("setup", "NO_TRADE")
    entry     = td.get("entry")
    target    = td.get("target")
    stop_loss = td.get("stop_loss")
    rejection = td.get("rejection")
    next_plan = td.get("next_plan")

    store.upsert_anchor(
        symbol=symbol,
        period_high=anchors.period_high,
        period_high_dt=anchors.period_high_dt,
        period_low=anchors.period_low,
        period_low_dt=anchors.period_low_dt,
        ma_50d=anchors.ma_50d,
        ma_20d=anchors.ma_20d,
        avg_vol_20d=anchors.avg_vol_20d,
        seed_start=window_start.isoformat(),
        seed_end=window_end.isoformat(),
    )
    store.upsert_narrative(
        symbol=symbol,
        last_date=window_end.isoformat(),
        seed_date=datetime.date.today().isoformat(),
        narrative=narrative_str,
    )
    store.insert_trade_decision(
        symbol=symbol,
        decision_date=next_td,
        direction=action,           # maps to existing DB column
        entry_price=entry,
        stop_loss=stop_loss,
        target_1=target,
        target_2=None,              # single target in new schema
        confidence=setup,           # B1/B2/.../S4/NO_TRADE — reuses confidence column
        rationale=rejection or json.dumps(td),
        next_plan=next_plan,
        raw_json=synthesis,
    )


# ---------------------------------------------------------------------------
# Batch mode orchestration (parallelise across symbols)
# ---------------------------------------------------------------------------

def run_batch_synthesis(
    symbols: list[str],
    store: PAStore,
    session_date: datetime.date,
    dry_run: bool,
) -> dict[str, list[str]]:
    """
    Batch-mode synthesis for all symbols.

    Single batch round-trip per run:
      Phase 1: load data for all symbols
      Phase 2: submit one full_synthesis request per symbol → one batch
      Phase 3: parse and persist all results
    """
    results: dict[str, list[str]] = {"success": [], "failed": []}

    # ── Phase 1: load data for all symbols ──────────────────────────────────
    log.info("[batch] Phase 1: loading data for %d symbols...", len(symbols))
    symbol_state: dict[str, dict] = {}

    for symbol in symbols:
        try:
            daily, weekly, h4, anchors, window_start, window_end = get_symbol_data_window(
                symbol=symbol,
                end_date=session_date,
                rolling_days=ROLLING_DAYS,
            )
            if len(daily) < 20:
                log.warning("%s: insufficient data — skipping", symbol)
                results["failed"].append(symbol)
                continue

            nifty_daily_b  = load_nifty_daily(window_start, window_end)
            vwap_4h_b      = build_vwap_block(daily, [], anchors.last_close)
            vp_b           = build_volume_profile_block(daily)
            nifty_ctx_b    = compute_nifty_context(daily, nifty_daily_b) if nifty_daily_b else None

            anchor_blk = build_anchor_block(
                symbol=symbol,
                seed_start=window_start.isoformat(),
                seed_end=window_end.isoformat(),
                period_high=anchors.period_high,
                period_high_dt=anchors.period_high_dt,
                period_low=anchors.period_low,
                period_low_dt=anchors.period_low_dt,
                ma_50d=anchors.ma_50d,
                ma_20d=anchors.ma_20d,
                avg_vol_20d=anchors.avg_vol_20d,
                last_close=anchors.last_close,
                last_date=anchors.last_date,
                vwap_4h=vwap_4h_b,
                volume_profile=vp_b,
                nifty_context=nifty_ctx_b,
            )

            n_chunks = (len(daily) + SEED_CHUNK_DAYS - 1) // SEED_CHUNK_DAYS
            symbol_state[symbol] = {
                "daily": daily, "weekly": weekly, "h4": h4,
                "anchors": anchors,
                "anchor_block": anchor_blk,
                "anchor_metrics_dict": {
                    "last_close": anchors.last_close,
                    "last_date":  anchors.last_date,
                    "ma_50d":     anchors.ma_50d,
                    "ma_20d":     anchors.ma_20d,
                    "avg_vol_20d": anchors.avg_vol_20d,
                },
                "vwap_block": vwap_4h_b,
                "volume_profile": vp_b,
                "nifty_ctx": nifty_ctx_b,
                "window_start": window_start,
                "window_end":   window_end,
                "next_td":      None,   # set in Phase 2
            }
            log.info("%s: loaded — %d days | %d chunks", symbol, len(daily), n_chunks)

        except Exception as e:
            log.error("%s: data load failed — %s", symbol, e, exc_info=True)
            results["failed"].append(symbol)

    if not symbol_state:
        return results

    # ── Pass 1: SmartEngine for every symbol (dual + smart batch modes) ───────
    if DECISION_ENGINE in ("dual", "smart"):
        log.info("[batch] Pass 1: SmartEngine for %d symbols", len(symbol_state))
        failed_pass1: list[str] = []
        for symbol, state in list(symbol_state.items()):
            if not state.get("next_td"):
                state["next_td"] = next_trading_day(state["window_end"]).isoformat()
            try:
                state["smart_synthesis"] = run_smart_synthesis_path(
                    symbol=symbol,
                    window_start=state["window_start"].isoformat(),
                    window_end=state["window_end"].isoformat(),
                    next_td=state["next_td"],
                    weekly_candles=state["weekly"],
                    daily_candles=state["daily"],
                    h4_candles=state["h4"],
                    anchor_metrics_dict=state["anchor_metrics_dict"],
                    vwap_block=state.get("vwap_block"),
                    volume_profile_block=state.get("volume_profile"),
                    nifty_ctx=state.get("nifty_ctx"),
                )
            except Exception as e:
                log.error("%s: SmartEngine Pass 1 failed — %s", symbol, e, exc_info=True)
                failed_pass1.append(symbol)
        for symbol in failed_pass1:
            symbol_state.pop(symbol, None)
            results["failed"].append(symbol)

    if not symbol_state:
        return results

    if dry_run:
        for symbol, state in symbol_state.items():
            if DECISION_ENGINE == "dual" and state.get("smart_synthesis"):
                smart = state["smart_synthesis"]
                smart_td = smart.get("trade_decision") or {}
                if smart_td.get("action") == "BUY":
                    synthesis = dict(smart)
                    synthesis["trade_decision"] = {
                        "action": "NO_TRADE",
                        "setup": "NO_TRADE",
                        "entry": None,
                        "target": None,
                        "stop_loss": None,
                        "rejection": "DRY RUN — SmartEngine BUY not confirmed by LLM",
                    }
                    synthesis["dual_pass"] = {
                        "pass1_engine": "smart",
                        "pass1": smart_td,
                        "pass2_engine": "llm",
                        "pass2": None,
                        "agreed": False,
                        "skipped_pass2": True,
                    }
                else:
                    synthesis = dict(smart)
                    synthesis["dual_pass"] = {
                        "pass1_engine": "smart",
                        "pass1": smart_td,
                        "pass2_engine": None,
                        "pass2": None,
                        "agreed": False,
                        "skipped_pass2": True,
                    }
            elif DECISION_ENGINE == "smart" and state.get("smart_synthesis"):
                synthesis = state["smart_synthesis"]
            else:
                synthesis = _dry_run_result(symbol)
            _persist(symbol=symbol, store=store, anchors=state["anchors"],
                     window_start=state["window_start"], window_end=state["window_end"],
                     next_td=state["next_td"],
                     synthesis=synthesis)
            results["success"].append(symbol)
        return results

    if DECISION_ENGINE == "smart":
        log.info("[batch] Persisting SmartEngine-only results")
        for symbol, state in symbol_state.items():
            try:
                synthesis = state["smart_synthesis"]
                _persist(symbol=symbol, store=store, anchors=state["anchors"],
                         window_start=state["window_start"], window_end=state["window_end"],
                         next_td=state["next_td"], synthesis=synthesis)
                td = synthesis.get("trade_decision") or {}
                log.info("%s: DONE ✓ [smart]  action=%s  entry=%s  target=%s  stop=%s",
                         symbol, td.get("action") or "NO_TRADE",
                         td.get("entry"), td.get("target"), td.get("stop_loss"))
                results["success"].append(symbol)
            except Exception as e:
                log.error("%s: persist failed — %s", symbol, e, exc_info=True)
                results["failed"].append(symbol)
        return results

    # ── Dual pass: LLM batch only for SmartEngine BUY + EOD filter pass ───────
    llm_candidates: dict[str, dict] = {}
    if DECISION_ENGINE == "dual":
        for symbol, state in symbol_state.items():
            smart = state.get("smart_synthesis") or {}
            smart_td = smart.get("trade_decision") or {}
            if _trade_action(smart) != "BUY":
                try:
                    synthesis = dict(smart)
                    synthesis["dual_pass"] = {
                        "pass1_engine": "smart",
                        "pass1": smart_td,
                        "pass2_engine": None,
                        "pass2": None,
                        "agreed": False,
                        "skipped_pass2": True,
                    }
                    _persist(symbol=symbol, store=store, anchors=state["anchors"],
                             window_start=state["window_start"],
                             window_end=state["window_end"],
                             next_td=state["next_td"], synthesis=synthesis)
                    log.info("%s: DONE ✓ [dual pass1]  action=%s — LLM skipped",
                             symbol, smart_td.get("action") or "NO_TRADE")
                    results["success"].append(symbol)
                except Exception as e:
                    log.error("%s: persist failed — %s", symbol, e, exc_info=True)
                    results["failed"].append(symbol)
                continue

            ok, filter_reason = qualifies_for_llm_analysis(state["daily"])
            if not ok:
                try:
                    synthesis = _dual_llm_filter_rejected(symbol, smart, filter_reason)
                    _persist(symbol=symbol, store=store, anchors=state["anchors"],
                             window_start=state["window_start"],
                             window_end=state["window_end"],
                             next_td=state["next_td"], synthesis=synthesis)
                    log.info("%s: DONE ✓ [dual EOD filter]  SmartEngine BUY — LLM skipped: %s",
                             symbol, filter_reason)
                    results["success"].append(symbol)
                except Exception as e:
                    log.error("%s: persist failed — %s", symbol, e, exc_info=True)
                    results["failed"].append(symbol)
                continue

            llm_candidates[symbol] = state

        if not llm_candidates:
            log.info("[batch] No LLM candidates (SmartEngine BUY + EOD filter) — Pass 2 skipped")
            return results
        log.info("[batch] Pass 2: LLM for %d candidates (SmartEngine BUY + green EOD ≥%.1f%%)",
                 len(llm_candidates), EOD_BUY_MIN_GAIN_PCT)
        symbol_state = llm_candidates

    elif DECISION_ENGINE == "llm":
        filtered_out: list[str] = []
        for symbol, state in list(symbol_state.items()):
            ok, filter_reason = qualifies_for_llm_analysis(state["daily"])
            if ok:
                continue
            try:
                if not state.get("next_td"):
                    state["next_td"] = next_trading_day(state["window_end"]).isoformat()
                synthesis = _llm_only_filter_rejected(symbol, filter_reason)
                _persist(symbol=symbol, store=store, anchors=state["anchors"],
                         window_start=state["window_start"],
                         window_end=state["window_end"],
                         next_td=state["next_td"],
                         synthesis=synthesis)
                log.info("%s: DONE ✓ [llm EOD filter]  skipped: %s", symbol, filter_reason)
                results["success"].append(symbol)
            except Exception as e:
                log.error("%s: persist failed — %s", symbol, e, exc_info=True)
                results["failed"].append(symbol)
            filtered_out.append(symbol)
        for symbol in filtered_out:
            symbol_state.pop(symbol, None)
        if not symbol_state:
            log.info("[batch] No symbols passed LLM EOD filter")
            return results
        log.info("[batch] LLM batch for %d symbols (green EOD ≥%.1f%%)",
                 len(symbol_state), EOD_BUY_MIN_GAIN_PCT)

    # ── Phase 2: LLM batch requests ──────────────────────────────────────────
    log.info("[batch] Phase 2: building synthesis requests for %d symbols", len(symbol_state))

    synthesis_requests: list[dict] = []
    for symbol, state in symbol_state.items():
        window_end = state["window_end"]
        next_td    = state["next_td"] or next_trading_day(window_end).isoformat()
        state["next_td"] = next_td

        user_msg = full_synthesis_prompt(
            symbol=symbol,
            window_start=state["window_start"].isoformat(),
            window_end=window_end.isoformat(),
            next_trading_date=next_td,
            anchor_block=state["anchor_block"],
            anchor_metrics=state["anchor_metrics_dict"],
            weekly_csv=candles_to_csv(state["weekly"]),
            daily_candles=state["daily"],
            h4_candles=state["h4"],
            chunk_size=SEED_CHUNK_DAYS,
        )

        req: dict = {
            "custom_id": f"{symbol}_synthesis",
            "system":    SYSTEM_PROMPT,
            "messages":  [{"role": "user", "content": user_msg}],
            "max_tokens": SEED_THINKING_BUDGET + SEED_MAX_TOKENS,
        }
        if SEED_USE_EXTENDED_THINKING:
            req["thinking"] = {"type": "enabled", "budget_tokens": SEED_THINKING_BUDGET}
        else:
            req["temperature"] = SEED_TEMPERATURE
        synthesis_requests.append(req)

    log.info("[batch] Phase 2: submitting %d synthesis requests", len(synthesis_requests))
    batch_id    = submit_batch(synthesis_requests)
    raw_results = collect_results(batch_id)

    # ── Phase 3: persist ─────────────────────────────────────────────────────
    log.info("[batch] Phase 3: persisting %d results...", len(symbol_state))
    for symbol, state in symbol_state.items():
        response_txt = raw_results.get(f"{symbol}_synthesis")
        if response_txt is None:
            log.error("%s: synthesis batch request failed", symbol)
            results["failed"].append(symbol)
            continue

        llm = _parse_json(response_txt, symbol, "batch_synthesis")
        if DECISION_ENGINE == "dual":
            smart = state.get("smart_synthesis") or {}
            synthesis = _merge_dual_pass(symbol, smart, llm)
        else:
            synthesis = llm

        try:
            _persist(symbol=symbol, store=store, anchors=state["anchors"],
                     window_start=state["window_start"], window_end=state["window_end"],
                     next_td=state["next_td"], synthesis=synthesis)
            td = synthesis.get("trade_decision") or {}
            tag = "dual" if DECISION_ENGINE == "dual" else "batch"
            log.info("%s: DONE ✓ [%s]  action=%s  entry=%s  target=%s  stop=%s",
                     symbol, tag, td.get("action") or "NO_TRADE",
                     td.get("entry"), td.get("target"), td.get("stop_loss"))
            results["success"].append(symbol)
        except Exception as e:
            log.error("%s: persist failed — %s", symbol, e, exc_info=True)
            results["failed"].append(symbol)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="PriceActionAgent — rolling synthesis (runs every evening post-market)"
    )
    p.add_argument("--symbols", type=str, default="",
                   help="Comma-separated symbols (default: all configured symbols)")
    p.add_argument("--date", type=str, default="",
                   help="Session date YYYY-MM-DD (default: last trading day)")
    p.add_argument("--dry-run", action="store_true",
                   help="Load data but skip API calls")
    p.add_argument("--batch", action="store_true",
                   help="Parallelise symbols via Anthropic Message Batches API")
    p.add_argument("--list-symbols", action="store_true",
                   help="Print configured symbols and exit")
    return p.parse_args()


def main():
    args = parse_args()

    if args.list_symbols:
        print(f"\n{len(NSE100_SYMBOLS)} symbols:")
        print(", ".join(NSE100_SYMBOLS))
        return

    if args.date:
        session_date = datetime.date.fromisoformat(args.date)
    else:
        from nse_calendar import last_trading_day
        session_date = last_trading_day()
    log.info("Session date: %s  |  Rolling window: %d trading days  |  engine=%s",
             session_date, ROLLING_DAYS, DECISION_ENGINE)

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols else NSE100_SYMBOLS
    )
    log.info("Symbols: %d", len(symbols))

    if args.dry_run:
        log.info("DRY RUN — no API calls")

    with PAStore() as store:
        if args.batch:
            log.info("BATCH mode — parallelising via Anthropic Message Batches API")
            results = run_batch_synthesis(
                symbols=symbols,
                store=store,
                session_date=session_date,
                dry_run=args.dry_run,
            )
        else:
            results: dict[str, list[str]] = {"success": [], "failed": []}
            for i, symbol in enumerate(symbols, 1):
                log.info("\n[%d/%d] %s", i, len(symbols), symbol)
                ok = synthesize_symbol(
                    symbol=symbol,
                    store=store,
                    session_date=session_date,
                    dry_run=args.dry_run,
                )
                (results["success"] if ok else results["failed"]).append(symbol)
                if i < len(symbols) and not args.dry_run:
                    time.sleep(INTER_SYMBOL_DELAY_S)

        summary = store.summary()

    log.info("\n" + "=" * 60)
    log.info("SYNTHESIS COMPLETE — session_date=%s", session_date)
    log.info("  Success: %d  |  Failed: %d — %s",
             len(results["success"]), len(results["failed"]), results["failed"])
    log.info("  DB: %s", summary)
    log.info("=" * 60)

    if results["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
