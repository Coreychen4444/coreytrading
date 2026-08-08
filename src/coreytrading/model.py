"""Deterministic feature extraction and current-state scoring."""

from __future__ import annotations

from typing import Protocol

from .domain import MarketSnapshot, ModelFeatures, Prediction, Regime


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


class ScoreModel(Protocol):
    def features(self, snapshot: MarketSnapshot) -> ModelFeatures: ...

    def predict(self, features: ModelFeatures) -> Prediction: ...


class TechnicalScoreModel:
    """Explainable score based on trend, distance, volume, and regime.

    The output describes current technical alignment. It deliberately does not
    claim to estimate expected return or probability of profit.
    """

    _REGIME_BIAS = {
        Regime.BULL: 1.0,
        Regime.NEUTRAL: 0.0,
        Regime.BEAR: -1.0,
    }

    def features(self, snapshot: MarketSnapshot) -> ModelFeatures:
        distance = (snapshot.close - snapshot.moving_average) / snapshot.atr
        return ModelFeatures(
            trend_strength=_clamp(distance / 3.0, -1.0, 1.0),
            normalized_distance=distance,
            volume_confirmation=_clamp(snapshot.volume_ratio - 1.0, -1.0, 1.0),
            regime_bias=self._REGIME_BIAS[snapshot.regime],
        )

    def predict(self, features: ModelFeatures) -> Prediction:
        score = _clamp(
            50.0
            + 25.0 * features.trend_strength
            + 15.0 * features.volume_confirmation
            + 10.0 * features.regime_bias,
            0.0,
            100.0,
        )
        confidence = _clamp(abs(score - 50.0) / 50.0, 0.0, 1.0)
        if score >= 75:
            label = "strong"
        elif score >= 60:
            label = "positive"
        elif score > 40:
            label = "neutral"
        else:
            label = "negative"
        return Prediction(score=round(score, 4), confidence=round(confidence, 4), label=label)
