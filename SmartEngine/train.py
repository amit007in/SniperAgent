"""Offline model training for SmartEngine LogisticScorer."""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _load_config():
    pa = Path(__file__).resolve().parent.parent / "PriceActionAgent"
    if str(pa) not in sys.path:
        sys.path.insert(0, str(pa))
    from config import NSE100_SYMBOLS, SMART_PARAMS, ROLLING_DAYS
    return NSE100_SYMBOLS, SMART_PARAMS, ROLLING_DAYS


def train_logistic(
    symbols: list[str] | None = None,
    end_date: datetime.date | None = None,
    walk_forward_splits: int = 3,
) -> Path:
    from SmartEngine.labeling import generate_labels_for_symbol, labels_to_arrays, save_labels
    from SmartEngine.scorer import LogisticScorer

    nse, smart_params, rolling_days = _load_config()
    symbols = symbols or nse[:10]
    end_date = end_date or datetime.date.today()
    model_dir = Path(smart_params["model_dir"])
    timeout = smart_params.get("label_timeout_bars", 10)

    all_labels = []
    for sym in symbols:
        log.info("Labeling %s...", sym)
        labs = generate_labels_for_symbol(sym, end_date, rolling_days, timeout)
        all_labels.extend(labs)
        log.info("  %d labeled signals", len(labs))

    save_labels(all_labels, model_dir / "labels.json")
    X, y = labels_to_arrays(all_labels)
    if len(y) < 20:
        log.warning("Insufficient labels (%d) — model not trained", len(y))
        return model_dir / "logistic.json"

    scorer = LogisticScorer()
    if walk_forward_splits > 1 and len(y) >= walk_forward_splits * 10:
        fold = len(y) // walk_forward_splits
        for i in range(walk_forward_splits - 1):
            tr_end = fold * (i + 1)
            scorer_fold = LogisticScorer()
            scorer_fold.fit(X[:tr_end], y[:tr_end])
            preds = [scorer_fold.predict_prob(x) for x in X[tr_end : tr_end + fold]]
            acc = sum((p >= 0.5) == bool(t) for p, t in zip(preds, y[tr_end : tr_end + fold])) / max(1, fold)
            log.info("Walk-forward fold %d accuracy: %.2f", i + 1, acc)

    scorer.fit(X, y)
    out = model_dir / "logistic.json"
    scorer.save(out)
    log.info("Saved logistic model to %s (%d samples, %d wins)", out, len(y), int(y.sum()))
    return out


def main():
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="Train SmartEngine logistic scorer")
    p.add_argument("--symbols", default=None, help="Comma-separated symbols")
    p.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    p.add_argument("--splits", type=int, default=3)
    args = p.parse_args()

    syms = args.symbols.split(",") if args.symbols else None
    end = datetime.date.fromisoformat(args.end_date) if args.end_date else None
    path = train_logistic(syms, end, args.splits)
    print(f"Model: {path}")


if __name__ == "__main__":
    main()
