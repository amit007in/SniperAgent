"""
PriceActionAgent — Anthropic Message Batches API helpers
=========================================================
Wraps the Anthropic Batch API for bulk processing of NSE symbols.

Usage pattern
-------------
1. Build a list of request dicts (see `BatchRequest` shape below).
2. Call `submit_batch(requests)` → returns batch_id.
3. Call `collect_results(batch_id)` → polls until done, returns {custom_id: text | None}.

BatchRequest shape
------------------
{
    "custom_id": str,          # unique per batch (e.g. "RELIANCE_chunk_0")
    "system":    str,          # system prompt
    "messages":  list[dict],   # full messages array (multi-turn history OK)
    "max_tokens": int,
    "temperature": float,      # optional, default 0.1
    "thinking": dict | None,   # optional extended-thinking config
}

Notes
-----
- `collect_results` returns None for a custom_id when the request errored/expired.
  Callers should treat None as a failed symbol and not persist anything for it.
- Extended thinking is supported: include {"type":"enabled","budget_tokens":N} in
  the "thinking" key and set temperature=1.0 (API requirement).
- The Batch API does not guarantee order — results are keyed by custom_id.
"""
import logging
import time

import anthropic

from config import (
    ANTHROPIC_MODEL,
    BATCH_MAX_WAIT_S,
    BATCH_POLL_INTERVAL_S,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal client
# ---------------------------------------------------------------------------

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

def submit_batch(requests: list[dict]) -> str:
    """
    Submit a batch of message requests.

    Parameters
    ----------
    requests : list of BatchRequest dicts (see module docstring).

    Returns
    -------
    batch_id : str
        The Anthropic batch ID to pass to collect_results().
    """
    client = _get_client()

    api_requests = []
    for req in requests:
        params: dict = {
            "model": ANTHROPIC_MODEL,
            "max_tokens": req["max_tokens"],
            "system": req["system"],
            "messages": req["messages"],
        }
        # Extended thinking (seed final synthesis)
        thinking = req.get("thinking")
        if thinking:
            params["thinking"] = thinking
            params["temperature"] = 1.0          # required by API when thinking enabled
            params["max_tokens"] = (
                thinking.get("budget_tokens", 8000) + req["max_tokens"]
            )
        else:
            params["temperature"] = req.get("temperature", 0.1)

        api_requests.append(
            anthropic.types.message_create_params.Request(
                custom_id=req["custom_id"],
                params=params,  # type: ignore[arg-type]
            )
        )

    batch = client.messages.batches.create(requests=api_requests)
    log.info(
        "Batch submitted: id=%s  requests=%d",
        batch.id, len(api_requests),
    )
    return batch.id


# ---------------------------------------------------------------------------
# Poll + collect
# ---------------------------------------------------------------------------

def poll_until_complete(batch_id: str) -> None:
    """
    Block until the batch reaches 'ended' status.
    Raises TimeoutError if BATCH_MAX_WAIT_S is exceeded.
    Logs progress every BATCH_POLL_INTERVAL_S seconds.
    """
    client = _get_client()
    waited = 0

    while waited < BATCH_MAX_WAIT_S:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        log.info(
            "Batch %s | status=%-12s | processing=%d  succeeded=%d  errored=%d  expired=%d",
            batch_id,
            batch.processing_status,
            counts.processing,
            counts.succeeded,
            counts.errored,
            counts.expired,
        )
        if batch.processing_status == "ended":
            return

        time.sleep(BATCH_POLL_INTERVAL_S)
        waited += BATCH_POLL_INTERVAL_S

    raise TimeoutError(
        f"Batch {batch_id} did not reach 'ended' within {BATCH_MAX_WAIT_S}s"
    )


def collect_results(batch_id: str) -> dict[str, str | None]:
    """
    Poll the batch to completion, then stream results.

    Returns
    -------
    dict mapping custom_id → response text (str) or None on error/expiry.
    """
    poll_until_complete(batch_id)

    client = _get_client()
    results: dict[str, str | None] = {}

    for result in client.messages.batches.results(batch_id):
        cid = result.custom_id

        match result.result.type:
            case "succeeded":
                # Extract first text block (skip thinking blocks)
                text = ""
                for block in result.result.message.content:
                    if block.type == "text":
                        text = block.text
                        break
                results[cid] = text

            case "errored":
                err = result.result.error
                log.error(
                    "Batch result %s ERRORED: type=%s  message=%s",
                    cid,
                    getattr(err, "type", "?"),
                    getattr(err, "message", str(err)),
                )
                results[cid] = None

            case "expired":
                log.error("Batch result %s EXPIRED (batch TTL exceeded)", cid)
                results[cid] = None

            case _:
                log.warning("Batch result %s: unknown result type %s", cid, result.result.type)
                results[cid] = None

    log.info(
        "Batch %s collected: total=%d  ok=%d  failed=%d",
        batch_id,
        len(results),
        sum(1 for v in results.values() if v is not None),
        sum(1 for v in results.values() if v is None),
    )
    return results
