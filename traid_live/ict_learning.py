from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .market import normalize_symbol
from .platform import PlatformStore


MIN_LEARNING_FORECASTS = 45
MAX_LEARNING_FORECASTS = 1200
_LABELS = ("bullish", "bearish", "no_trade")
_CACHE_LOCK = threading.RLock()
_MODEL_CACHE: dict[tuple[str, str, int, str], "FittedContextModel"] = {}


def _safe(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _one(value: Any, expected: str) -> float:
    return 1.0 if str(value or "").lower() == expected else 0.0


def feature_vector(context: dict[str, Any], market_context: dict[str, Any] | None = None) -> np.ndarray:
    market = market_context or {}
    structure = context.get("structure") or {}
    liquidity = context.get("liquidity") or {}
    sweep = liquidity.get("sweep") or {}
    displacement = context.get("displacement") or {}
    dealing = context.get("dealing_range") or {}
    session = context.get("session") or {}
    event = context.get("event_risk") or {}
    setup = context.get("setup") or {}
    fvg = context.get("fair_value_gaps") or {}
    model = context.get("context_model") or {}
    volatility = market.get("volatility") or {}

    structure_bias = structure.get("bias")
    sweep_direction = sweep.get("direction")
    displacement_direction = displacement.get("direction")
    setup_bias = setup.get("bias")
    session_name = session.get("name")
    zone = dealing.get("zone")

    return np.asarray(
        [
            1.0,
            _one(structure_bias, "bullish"),
            _one(structure_bias, "bearish"),
            _one(structure_bias, "sideways"),
            _safe(structure.get("strength_pct")) / 100,
            1.0 if structure.get("bos") else 0.0,
            1.0 if structure.get("choch") else 0.0,
            _one(sweep_direction, "bullish"),
            _one(sweep_direction, "bearish"),
            _one(displacement_direction, "bullish"),
            _one(displacement_direction, "bearish"),
            _safe(displacement.get("score_pct")) / 100,
            _one(zone, "discount"),
            _one(zone, "premium"),
            _one(zone, "equilibrium"),
            _safe(dealing.get("position_pct"), 50.0) / 100,
            1.0 if fvg.get("nearest_bullish") else 0.0,
            1.0 if fvg.get("nearest_bearish") else 0.0,
            1.0 if context.get("order_block") else 0.0,
            _one(setup_bias, "bullish"),
            _one(setup_bias, "bearish"),
            _safe(setup.get("quality_pct")) / 100,
            _one(session_name, "asian"),
            _one(session_name, "london"),
            _one(session_name, "new_york"),
            _one(session_name, "after_hours"),
            1.0 if session.get("killzone") else 0.0,
            1.0 if event.get("blocked") else 0.0,
            _safe(volatility.get("atr_pct")) / 5.0,
            _safe(volatility.get("relative_pct"), 100.0) / 300.0,
            _safe(model.get("bullish_probability_pct"), 33.3) / 100,
            _safe(model.get("bearish_probability_pct"), 33.3) / 100,
            _safe(model.get("no_trade_probability_pct"), 33.3) / 100,
        ],
        dtype=float,
    )


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=1, keepdims=True)
    exponentials = np.exp(np.clip(shifted, -40, 40))
    return exponentials / np.maximum(exponentials.sum(axis=1, keepdims=True), 1e-12)


@dataclass(frozen=True)
class FittedContextModel:
    mean: np.ndarray
    scale: np.ndarray
    weights: np.ndarray
    samples: int
    class_counts: dict[str, int]
    last_scored_at: str

    def predict(self, vector: np.ndarray) -> np.ndarray:
        normalized = (vector - self.mean) / self.scale
        return _softmax(normalized[None, :] @ self.weights)[0]


def _actual_label(actual_close: float, base_close: float, threshold_pct: float) -> str:
    move_pct = (actual_close - base_close) / max(abs(base_close), 1e-12) * 100
    if move_pct > threshold_pct:
        return "bullish"
    if move_pct < -threshold_pct:
        return "bearish"
    return "no_trade"


def _training_rows(
    store: PlatformStore,
    *,
    symbol: str,
    timeframe: str,
    horizon: int,
) -> tuple[np.ndarray, np.ndarray, str] | None:
    with store.connection() as connection:
        rows = connection.execute(
            """
            SELECT s.forecast_id,s.actual_json,s.scored_at,f.revision_json
            FROM forecast_scores s
            JOIN forecasts f ON f.id=s.forecast_id
            WHERE f.symbol=? AND f.timeframe=? AND s.horizon=?
            ORDER BY s.scored_at DESC
            LIMIT ?
            """,
            (normalize_symbol(symbol), timeframe, int(horizon), MAX_LEARNING_FORECASTS),
        ).fetchall()

    independent: dict[str, tuple[np.ndarray, int, str]] = {}
    for row in rows:
        try:
            revision = json.loads(row["revision_json"]) if row["revision_json"] else {}
            market_context = revision.get("market_context") or {}
            context = revision.get("ict_context") or market_context.get("ict") or {}
            intrabar = revision.get("intrabar") or {}
            actual = json.loads(row["actual_json"])
            base = float(intrabar["close"])
            actual_close = float(actual["close"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not context or context.get("version") != "ict_smc_v1":
            continue
        atr_pct = _safe((market_context.get("volatility") or {}).get("atr_pct"))
        threshold = max(atr_pct * 0.10, 0.002)
        label = _actual_label(actual_close, base, threshold)
        independent[row["forecast_id"]] = (
            feature_vector(context, market_context),
            _LABELS.index(label),
            str(row["scored_at"]),
        )

    if len(independent) < MIN_LEARNING_FORECASTS:
        return None
    values = list(independent.values())
    labels = np.asarray([value[1] for value in values], dtype=int)
    if len(set(labels.tolist())) < 2:
        return None
    features = np.asarray([value[0] for value in values], dtype=float)
    last_scored = max(value[2] for value in values)
    return features, labels, last_scored


def _fit(features: np.ndarray, labels: np.ndarray, last_scored: str) -> FittedContextModel:
    mean = features.mean(axis=0)
    scale = features.std(axis=0)
    mean[0] = 0.0
    scale[0] = 1.0
    scale = np.where(scale < 1e-6, 1.0, scale)
    x = (features - mean) / scale
    classes = len(_LABELS)
    weights = np.zeros((x.shape[1], classes), dtype=float)
    one_hot = np.eye(classes, dtype=float)[labels]
    counts = np.bincount(labels, minlength=classes).astype(float)
    sample_weights = np.asarray([len(labels) / max(classes * counts[label], 1.0) for label in labels])
    sample_weights /= max(sample_weights.mean(), 1e-12)

    learning_rate = 0.12
    regularization = 0.018
    for iteration in range(220):
        probabilities = _softmax(x @ weights)
        error = (probabilities - one_hot) * sample_weights[:, None]
        gradient = x.T @ error / len(x) + regularization * weights
        weights -= (learning_rate / math.sqrt(1 + iteration * 0.025)) * gradient

    class_counts = {label: int(counts[index]) for index, label in enumerate(_LABELS)}
    return FittedContextModel(
        mean=mean,
        scale=scale,
        weights=weights,
        samples=len(labels),
        class_counts=class_counts,
        last_scored_at=last_scored,
    )


def adaptive_context_model(
    store: PlatformStore,
    *,
    symbol: str,
    timeframe: str,
    horizon: int,
    context: dict[str, Any],
    market_context: dict[str, Any],
    heuristic: dict[str, Any],
) -> dict[str, Any]:
    training = _training_rows(
        store,
        symbol=symbol,
        timeframe=timeframe,
        horizon=horizon,
    )
    if training is None:
        return {
            **heuristic,
            "mode": "heuristic_warmup",
            "learned": False,
            "required_forecasts": MIN_LEARNING_FORECASTS,
        }

    features, labels, last_scored = training
    key = (normalize_symbol(symbol), timeframe, len(labels), last_scored)
    with _CACHE_LOCK:
        model = _MODEL_CACHE.get(key)
        if model is None:
            model = _fit(features, labels, last_scored)
            _MODEL_CACHE.clear()
            _MODEL_CACHE[key] = model

    learned = model.predict(feature_vector(context, market_context))
    heuristic_values = np.asarray(
        [
            _safe(heuristic.get("bullish_probability_pct"), 33.3),
            _safe(heuristic.get("bearish_probability_pct"), 33.3),
            _safe(heuristic.get("no_trade_probability_pct"), 33.3),
        ],
        dtype=float,
    ) / 100
    blended = learned * 0.65 + heuristic_values * 0.35
    if (context.get("event_risk") or {}).get("blocked"):
        blended[2] += 0.50
        blended /= blended.sum()
    probabilities = {label: round(float(blended[index]) * 100, 1) for index, label in enumerate(_LABELS)}
    dominant = max(probabilities, key=probabilities.get)
    return {
        "bullish_probability_pct": probabilities["bullish"],
        "bearish_probability_pct": probabilities["bearish"],
        "no_trade_probability_pct": probabilities["no_trade"],
        "dominant": dominant,
        "mode": "adaptive_logistic",
        "learned": True,
        "training_forecasts": model.samples,
        "class_counts": model.class_counts,
        "last_scored_at": model.last_scored_at,
        "heuristic_weight_pct": 35,
        "learned_weight_pct": 65,
    }
