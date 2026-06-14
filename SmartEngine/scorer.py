"""Pluggable evidence scorers: Rule, Logistic, Hermes."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

import numpy as np

from SmartAgent.quantcore import sigmoid
from SmartEngine.setups.base import Signal

log = logging.getLogger(__name__)

FEATURE_NAMES = [
    "rr", "vol_ratio", "mom_tstat", "vr_1d", "trend_conf",
    "align_score", "setup_base", "gap_pct",
]


class Scorer(Protocol):
    def predict(self, signal: Signal, states: dict, feats) -> float: ...
    def learn(self, signal: Signal, outcome: dict) -> None: ...


class RuleScorer:
    """Transparent weighted evidence count — ships by default."""

    def predict(self, signal: Signal, states: dict, feats) -> float:
        base = signal.score
        rr_bonus = min(signal.rr, 3.0) * 0.15
        vol_bonus = min(feats.last_bar_vol_ratio, 3.0) * 0.08
        mom_bonus = max(0.0, feats.mom_tstat_1d) * 0.05
        vr = feats.vr_1d
        vr_bonus = (max(0.0, vr) if signal.direction == "BUY" else max(0.0, -vr)) * 0.08
        conf = states.get("1d").trend_confidence if states.get("1d") else 0.5
        raw = base + rr_bonus + vol_bonus + mom_bonus + vr_bonus + conf * 0.5
        return float(min(1.0, raw / 5.0))

    def learn(self, signal: Signal, outcome: dict) -> None:
        pass


class LogisticScorer:
    """Calibrated logistic regression on labeled outcomes."""

    def __init__(self, model_path: Path | None = None) -> None:
        self.coef: np.ndarray | None = None
        self.intercept: float = 0.0
        self.feature_names = FEATURE_NAMES
        if model_path and model_path.exists():
            self.load(model_path)

    def _features(self, signal: Signal, states: dict, feats) -> np.ndarray:
        s1d = states.get("1d")
        s1w = states.get("1w")
        s4h = states.get("4h")
        align = 1.0 if s1w and s1d and s4h and s1w.trend == s1d.trend == s4h.trend else 0.0
        return np.array([
            signal.rr,
            feats.last_bar_vol_ratio,
            feats.mom_tstat_1d,
            feats.vr_1d,
            s1d.trend_confidence if s1d else 0.5,
            align,
            signal.score,
            feats.gap_pct,
        ], dtype=float)

    def predict(self, signal: Signal, states: dict, feats) -> float:
        if self.coef is None:
            return RuleScorer().predict(signal, states, feats)
        x = self._features(signal, states, feats)
        return self.predict_prob(x)

    def predict_prob(self, x) -> float:
        if self.coef is None:
            return 0.5
        z = float(self.intercept + self.coef @ np.asarray(x))
        return float(sigmoid(z))

    def learn(self, signal: Signal, outcome: dict) -> None:
        pass

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler

            scaler = StandardScaler()
            Xs = scaler.fit_transform(X)
            clf = LogisticRegression(max_iter=500, class_weight="balanced")
            clf.fit(Xs, y)
            self.coef = clf.coef_[0] / scaler.scale_
            self.intercept = float(clf.intercept_[0] - (clf.coef_[0] / scaler.scale_) @ scaler.mean_)
            self._scaler_mean = scaler.mean_
            self._scaler_scale = scaler.scale_
        except ImportError:
            # Fallback: closed-form-ish single-feature proxy
            self.coef = np.ones(X.shape[1]) / X.shape[1]
            self.intercept = 0.0

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "coef": self.coef.tolist() if self.coef is not None else None,
            "intercept": self.intercept,
            "feature_names": self.feature_names,
        }
        path.write_text(json.dumps(data, indent=2))

    def load(self, path: Path) -> None:
        data = json.loads(path.read_text())
        self.coef = np.array(data["coef"]) if data.get("coef") else None
        self.intercept = float(data.get("intercept", 0.0))
        self.feature_names = data.get("feature_names", FEATURE_NAMES)


class HermesLearnerScorer:
    """Optional wrapper around Hermes HorizonLearner — gated on PA_SMART_SCORER=hermes."""

    def __init__(self) -> None:
        self._learner = None
        self._features: list[str] = []
        self._init_learner()

    def _init_learner(self) -> None:
        try:
            from SmartAgent.allstrategy import FEATURES, HORIZONS, HorizonLearner

            hcfg = HORIZONS.get("swing") or HORIZONS.get("positional")
            if hcfg:
                self._learner = HorizonLearner("pa_swing", hcfg)
                self._features = FEATURES
        except Exception as e:
            log.warning("HermesLearnerScorer unavailable: %s", e)
            self._learner = None

    def _phi(self, signal: Signal, states: dict, feats) -> dict:
        s1d = states.get("1d")
        s1w = states.get("1w")
        s4h = states.get("4h")
        align = 1.0 if s1w and s1d and s4h and s1w.trend == s1d.trend == s4h.trend else 0.0
        g = sigmoid(feats.vr_1d)
        base = {
            "structure": min(1.0, signal.score / 4.0),
            "value": 0.5 if s1d and s1d.vp_position == "INSIDE_VA" else 0.7,
            "vol_anom": min(1.0, abs(feats.vol_z_1d) / 3.0),
            "mom_t": np.clip(feats.mom_tstat_1d, -2, 2) / 2,
            "vratio": np.clip(feats.vr_1d, -1, 1),
            "flow": 0.0,
            "pcr": 0.0,
            "vrp": 0.0,
            "maturity": 0.5,
            "accel": 0.5,
            "align": align,
            "mom_l": np.clip(feats.mom_tstat_1d, -2, 2) / 2,
            "mom_v": min(1.0, feats.last_bar_vol_ratio / 3.0),
        }
        if self._learner:
            return {k: base.get(k, 0.0) for k in self._features}
        return base

    def predict(self, signal: Signal, states: dict, feats) -> float:
        if not self._learner:
            return RuleScorer().predict(signal, states, feats)
        phi = self._phi(signal, states, feats)
        g = sigmoid(feats.vr_1d)
        L = self._learner.log_odds(phi, g=g)
        return self._learner.calibrate(L)

    def learn(self, signal: Signal, outcome: dict) -> None:
        if not self._learner:
            return
        phi = outcome.get("phi") or self._phi(signal, {}, outcome.get("feats"))
        trade = {
            "r_multiple": outcome.get("r_multiple", 0.0),
            "phi": phi,
            "g_entry": outcome.get("g_entry", 0.5),
            "L_entry": outcome.get("L_entry"),
        }
        self._learner.learn(trade)


def make_scorer(smart_params: dict) -> Scorer:
    kind = smart_params.get("scorer", "rule").lower()
    model_dir = Path(smart_params.get("model_dir", "SmartEngine/models"))
    if kind == "logistic":
        return LogisticScorer(model_dir / "logistic.json")
    if kind == "hermes":
        return HermesLearnerScorer()
    return RuleScorer()


def select_best(
    signals: list[Signal],
    feats,
    params: dict,
    states: dict,
    scorer: Scorer,
    smart_params: dict,
) -> Signal | None:
    if not signals:
        return None

    min_prob = smart_params.get("smart_min_prob", 0.55)
    min_rr = params.get("min_rr_ratio", 1.0)

    scored: list[tuple[float, Signal]] = []
    for s in signals:
        prob = scorer.predict(s, states, feats)
        s.score = prob
        if prob >= min_prob and s.rr >= min_rr:
            scored.append((prob, s))

    if not scored:
        return None

    scored.sort(key=lambda x: (-x[0], -x[1].rr))
    return scored[0][1]
