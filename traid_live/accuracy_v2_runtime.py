from __future__ import annotations

import json
import math
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from . import accuracy_runtime as accuracy
from . import ict_runtime as ict
from . import intelligence_v2 as v2
from .market import normalize_symbol

ENGINE_VERSION = "macro_gbt_v2"
GBT_MIN_SAMPLES = 1000
GBT_MIN_REPLAYS = 50
GBT_VALIDATION_FRACTION = 0.20
GBT_ESTIMATORS = 72
GBT_LEARNING_RATE = 0.055
GBT_MAX_DEPTH = 3
GBT_MIN_LEAF = 24
GBT_MAX_THRESHOLDS = 12
MACRO_TIMEOUT_SECONDS = 1.6
LIVE_MACRO_TTL_SECONDS = 75.0

_BASE_FEATURE_VECTOR = accuracy._feature_vector
_BASE_MATCHING_CACHE = v2._matching_cache

MACRO_TICKERS: dict[str, tuple[str, str]] = {
    "DXY": ("DX-Y.NYB", "US Dollar Index"),
    "VIX": ("^VIX", "CBOE Volatility Index"),
    "US10Y": ("^TNX", "US 10Y Treasury yield"),
    "US2Y": ("2YY=F", "US 2Y yield futures"),
}

TARGET_MACRO_WEIGHTS: dict[str, dict[str, float]] = {
    "XAUUSD": {"DXY": 1.00, "VIX": 0.45, "US10Y": 0.95, "US2Y": 0.85},
    "XAGUSD": {"DXY": 0.90, "VIX": 0.45, "US10Y": 0.75, "US2Y": 0.65},
    "EURUSD": {"DXY": 1.00, "VIX": 0.25, "US10Y": 0.65, "US2Y": 0.80},
    "USDJPY": {"DXY": 0.70, "VIX": 0.55, "US10Y": 0.85, "US2Y": 1.00},
    "NAS100": {"DXY": 0.35, "VIX": 1.00, "US10Y": 0.95, "US2Y": 0.75},
    "SPX500": {"DXY": 0.30, "VIX": 1.00, "US10Y": 0.80, "US2Y": 0.65},
}

EXTRA_FEATURE_NAMES = (
    "macro_dxy",
    "macro_vix",
    "macro_us10y",
    "macro_us2y",
    "yield_curve_signal",
)
accuracy.FEATURE_NAMES = tuple(dict.fromkeys((*accuracy.FEATURE_NAMES, *EXTRA_FEATURE_NAMES)))

_MODEL_LOCK = threading.RLock()
_FAST_MODEL_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_GBT_OBJECTS: dict[str, "GradientBoostedTrees"] = {}

_MACRO_LOCK = threading.RLock()
_MACRO_CACHE: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="traid-context")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _timeframe_interval(timeframe: str) -> str:
    return {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "60m",
        "4h": "60m",
        "1d": "1d",
    }.get(timeframe, "5m")


def _chart_window_seconds(interval: str) -> int:
    return {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "60m": 3600,
        "1d": 86400,
    }.get(interval, 300)


def _fetch_yahoo_frame(ticker: str, timeframe: str, cutoff: pd.Timestamp) -> pd.DataFrame:
    interval = _timeframe_interval(timeframe)
    step = _chart_window_seconds(interval)
    period2 = int(cutoff.timestamp()) + 1
    period1 = period2 - max(step * 260, 7 * 86400)
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(ticker, safe='')}"
        f"?period1={period1}&period2={period2}&interval={interval}"
        "&includePrePost=true&events=div%2Csplits"
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 Traid/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=MACRO_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return pd.DataFrame()
    timestamps = result.get("timestamp") or []
    quote = ((((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or [])
    rows = []
    for timestamp, close in zip(timestamps, quote):
        if close is None:
            continue
        rows.append({"timestamp": pd.to_datetime(int(timestamp), unit="s", utc=True), "close": float(close)})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def _macro_frame(name: str, timeframe: str, cutoff: pd.Timestamp) -> pd.DataFrame:
    ticker = MACRO_TICKERS[name][0]
    live = abs((pd.Timestamp.now(tz="UTC") - cutoff).total_seconds()) < 900
    cache_key = (ticker, timeframe)
    if live:
        with _MACRO_LOCK:
            cached = _MACRO_CACHE.get(cache_key)
            if cached and time.monotonic() - cached[0] <= LIVE_MACRO_TTL_SECONDS:
                return cached[1].copy()
    frame = _fetch_yahoo_frame(ticker, timeframe, cutoff)
    if live and not frame.empty:
        with _MACRO_LOCK:
            _MACRO_CACHE[cache_key] = (time.monotonic(), frame.copy())
    return frame


def _prewarm_macro_cache() -> None:
    cutoff = pd.Timestamp.now(tz="UTC")
    for timeframe in ("5m", "1h"):
        futures = [_EXECUTOR.submit(_macro_frame, name, timeframe, cutoff) for name in MACRO_TICKERS]
        for future in futures:
            try:
                future.result()
            except Exception:
                pass


def _prewarm_loop() -> None:
    while True:
        try:
            _prewarm_macro_cache()
        except Exception:
            pass
        time.sleep(60)


threading.Thread(target=_prewarm_loop, daemon=True, name="traid-macro-prefetch").start()


def _clean_reference(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    clean = frame.copy()
    clean["timestamp"] = pd.to_datetime(clean["timestamp"], utc=True, errors="coerce")
    clean["close"] = pd.to_numeric(clean["close"], errors="coerce")
    return clean.dropna(subset=["timestamp", "close"]).sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)


def _reference_contribution(base: pd.DataFrame, reference: pd.DataFrame, *, relevance_multiplier: float = 1.0) -> dict[str, Any] | None:
    reference = _clean_reference(reference)
    if reference.empty or len(base) < 12:
        return None
    spacing = base["timestamp"].diff().dropna().dt.total_seconds().median() if len(base) > 2 else 300
    tolerance_seconds = max(90, int(max(spacing, 60)) * 2)
    merged = pd.merge_asof(
        base.sort_values("timestamp"),
        reference[["timestamp", "close"]].rename(columns={"close": "peer_close"}).sort_values("timestamp"),
        on="timestamp",
        direction="backward",
        tolerance=pd.Timedelta(seconds=tolerance_seconds),
    ).dropna(subset=["peer_close"]).tail(140)
    if len(merged) < 12:
        return None
    base_returns = merged["base_close"].pct_change()
    peer_returns = merged["peer_close"].pct_change()
    valid = pd.concat([base_returns, peer_returns], axis=1).dropna().tail(80)
    if len(valid) < 8:
        return None
    correlation = _safe_float(valid.iloc[:, 0].corr(valid.iloc[:, 1]))
    if abs(correlation) < 0.08:
        return None
    lookback = min(3, len(merged) - 1)
    start = _safe_float(merged["peer_close"].iloc[-1 - lookback])
    end = _safe_float(merged["peer_close"].iloc[-1])
    if abs(start) < 1e-12:
        return None
    move_pct = (end - start) / abs(start) * 100
    volatility_pct = max(_safe_float(peer_returns.tail(40).std()) * 100 * math.sqrt(max(lookback, 1)), 0.002)
    normalized_move = math.tanh(move_pct / max(volatility_pct * 1.5, 0.002))
    contribution = math.copysign(1.0, correlation) * normalized_move
    relevance = abs(correlation) * max(0.0, relevance_multiplier)
    return {
        "correlation": correlation,
        "move_pct": move_pct,
        "normalized_move": normalized_move,
        "contribution": contribution,
        "relevance": relevance,
        "samples": int(len(valid)),
    }


def cross_market_context_v2(frame: pd.DataFrame, state: dict[str, Any]) -> dict[str, Any]:
    platform = state.get("platform")
    symbol = normalize_symbol(str(state.get("symbol") or "XAUUSD"))
    timeframe = str(state.get("timeframe") or "5m")
    base_frame = accuracy._clean_candles(frame)
    if platform is None or len(base_frame) < 12:
        return {"version": accuracy.RUNTIME_VERSION, "engine_version": ENGINE_VERSION, "available": False, "bias": "neutral", "signal": 0.0, "strength_pct": 0.0, "peers": [], "macro": {}}
    cache = state.setdefault("_cross_market_v2_cache", {})
    cutoff = pd.to_datetime(base_frame["timestamp"].iloc[-1], utc=True)
    cache_key = (symbol, timeframe, cutoff.isoformat())
    cached = cache.get(cache_key)
    if cached is not None:
        return dict(cached)
    base = base_frame[["timestamp", "close"]].rename(columns={"close": "base_close"}).tail(180)
    tasks: dict[Any, tuple[str, str]] = {}
    for peer in accuracy.PEER_MAP.get(symbol, ()):
        tasks[_EXECUTOR.submit(platform.engine.provider.get_candles, peer, timeframe, 180)] = ("market", peer)
    for name in MACRO_TICKERS:
        tasks[_EXECUTOR.submit(_macro_frame, name, timeframe, cutoff)] = ("macro", name)
    details: list[dict[str, Any]] = []
    macro: dict[str, dict[str, Any]] = {}
    weighted_signal = 0.0
    weight_total = 0.0
    macro_weights = TARGET_MACRO_WEIGHTS.get(symbol, {})
    for future, (kind, name) in tasks.items():
        try:
            reference = future.result()
            multiplier = 1.0 if kind == "market" else macro_weights.get(name, 0.5)
            result = _reference_contribution(base, reference, relevance_multiplier=multiplier)
            if result is None:
                continue
            weighted_signal += result["contribution"] * result["relevance"]
            weight_total += result["relevance"]
            item = {
                "symbol": name,
                "kind": kind,
                "correlation": round(result["correlation"], 4),
                "move_pct": round(result["move_pct"], 4),
                "normalized_move": round(result["normalized_move"], 4),
                "contribution": round(result["contribution"], 4),
                "relevance_pct": round(result["relevance"] * 100, 1),
                "samples": result["samples"],
            }
            if kind == "macro":
                item["ticker"] = MACRO_TICKERS[name][0]
                item["label"] = MACRO_TICKERS[name][1]
                macro[name] = dict(item)
            details.append(item)
        except Exception as exc:
            details.append({"symbol": name, "kind": kind, "available": False, "detail": str(exc)})
    signal = weighted_signal / weight_total if weight_total > 0 else 0.0
    signal = max(-1.0, min(1.0, signal))
    bias = "bullish" if signal >= 0.15 else "bearish" if signal <= -0.15 else "neutral"
    payload = {
        "version": accuracy.RUNTIME_VERSION,
        "engine_version": ENGINE_VERSION,
        "available": weight_total > 0,
        "bias": bias,
        "signal": round(signal, 4),
        "strength_pct": round(abs(signal) * 100, 1),
        "peer_count": sum(1 for item in details if item.get("available", True)),
        "peers": details,
        "macro": macro,
        "method": "parallel_dynamic_correlation_x_normalized_momentum",
        "completed_candles_only": True,
        "macro_source": "Yahoo Finance chart feed (best effort)",
    }
    cache[cache_key] = dict(payload)
    return payload


def feature_vector_v2(item: dict[str, Any], path: pd.DataFrame, *, base: float, context: dict[str, Any], cross_alignment: float) -> dict[str, float]:
    features = dict(_BASE_FEATURE_VECTOR(item, path, base=base, context=context, cross_alignment=cross_alignment))
    cross = context.get("cross_market") or ((context.get("ict") or {}).get("cross_market")) or {}
    macro = cross.get("macro") or {}
    def contribution(name: str) -> float:
        return max(-1.0, min(1.0, _safe_float((macro.get(name) or {}).get("contribution"))))
    dxy = contribution("DXY")
    vix = contribution("VIX")
    us10y = contribution("US10Y")
    us2y = contribution("US2Y")
    features.update({"macro_dxy": dxy, "macro_vix": vix, "macro_us10y": us10y, "macro_us2y": us2y, "yield_curve_signal": max(-1.0, min(1.0, us10y - us2y))})
    return features


@dataclass
class _Node:
    value: float
    feature: int = -1
    threshold: float = 0.0
    left: "_Node | None" = None
    right: "_Node | None" = None
    @property
    def leaf(self) -> bool:
        return self.feature < 0 or self.left is None or self.right is None


class RegressionTree:
    def __init__(self, max_depth: int = 3, min_leaf: int = 24, max_thresholds: int = 12):
        self.max_depth = max_depth
        self.min_leaf = min_leaf
        self.max_thresholds = max_thresholds
        self.root: _Node | None = None
    def fit(self, x: np.ndarray, y: np.ndarray) -> "RegressionTree":
        self.root = self._build(x, y, 0)
        return self
    def _build(self, x: np.ndarray, y: np.ndarray, depth: int) -> _Node:
        value = float(np.mean(y)) if len(y) else 0.0
        node = _Node(value=value)
        if depth >= self.max_depth or len(y) < self.min_leaf * 2 or float(np.var(y)) < 1e-8:
            return node
        parent_sse = float(np.sum((y - value) ** 2))
        best_gain = 0.0
        best: tuple[int, float, np.ndarray] | None = None
        for feature in range(x.shape[1]):
            column = x[:, feature]
            finite = column[np.isfinite(column)]
            if len(finite) < self.min_leaf * 2:
                continue
            thresholds = np.unique(np.quantile(finite, np.linspace(0.08, 0.92, self.max_thresholds)))
            for threshold in thresholds:
                mask = column <= threshold
                left_n = int(mask.sum())
                right_n = len(y) - left_n
                if left_n < self.min_leaf or right_n < self.min_leaf:
                    continue
                left_y = y[mask]
                right_y = y[~mask]
                sse = float(np.sum((left_y - left_y.mean()) ** 2) + np.sum((right_y - right_y.mean()) ** 2))
                gain = parent_sse - sse
                if gain > best_gain:
                    best_gain = gain
                    best = (feature, float(threshold), mask)
        if best is None or best_gain <= max(1e-7, parent_sse * 1e-5):
            return node
        feature, threshold, mask = best
        node.feature = feature
        node.threshold = threshold
        node.left = self._build(x[mask], y[mask], depth + 1)
        node.right = self._build(x[~mask], y[~mask], depth + 1)
        return node
    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.root is None:
            return np.zeros(len(x), dtype=float)
        return np.asarray([self._predict_row(row, self.root) for row in x], dtype=float)
    def _predict_row(self, row: np.ndarray, node: _Node) -> float:
        current = node
        while not current.leaf:
            current = current.left if row[current.feature] <= current.threshold else current.right
        return current.value


class GradientBoostedTrees:
    def __init__(self, estimators: int = GBT_ESTIMATORS, learning_rate: float = GBT_LEARNING_RATE, max_depth: int = GBT_MAX_DEPTH, min_leaf: int = GBT_MIN_LEAF):
        self.estimators = estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.min_leaf = min_leaf
        self.base = 50.0
        self.trees: list[RegressionTree] = []
    def fit(self, x: np.ndarray, y: np.ndarray) -> "GradientBoostedTrees":
        self.base = float(np.mean(y))
        prediction = np.full(len(y), self.base, dtype=float)
        self.trees = []
        for _ in range(self.estimators):
            residual = y - prediction
            tree = RegressionTree(max_depth=self.max_depth, min_leaf=self.min_leaf, max_thresholds=GBT_MAX_THRESHOLDS).fit(x, residual)
            update = tree.predict(x)
            if float(np.std(update)) < 1e-7:
                break
            prediction += self.learning_rate * update
            self.trees.append(tree)
        return self
    def predict(self, x: np.ndarray) -> np.ndarray:
        prediction = np.full(len(x), self.base, dtype=float)
        for tree in self.trees:
            prediction += self.learning_rate * tree.predict(x)
        return np.clip(prediction, 0.0, 100.0)


def _training_signature(target: Any, symbol: str, timeframe: str) -> tuple[int, int, int]:
    accuracy._ensure_schema(target)
    canonical = normalize_symbol(symbol)
    replay_cutoff = getattr(target, "replay_cutoff", None)
    with target.connection() as connection:
        if replay_cutoff is None:
            row = connection.execute("SELECT COUNT(*) AS samples, COUNT(DISTINCT cutoff_timestamp) AS replays, COALESCE(MAX(id),0) AS max_id FROM path_learning_samples WHERE symbol=? AND timeframe=?", (canonical, timeframe)).fetchone()
        else:
            cutoff_iso = replay_cutoff.isoformat()
            row = connection.execute("SELECT COUNT(*) AS samples, COUNT(DISTINCT cutoff_timestamp) AS replays, COALESCE(MAX(id),0) AS max_id FROM path_learning_samples WHERE symbol=? AND timeframe=? AND outcome_end_timestamp IS NOT NULL AND outcome_end_timestamp<=?", (canonical, timeframe, cutoff_iso)).fetchone()
    return int(row["samples"] or 0), int(row["replays"] or 0), int(row["max_id"] or 0)


def _decode_rows(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    x_rows: list[list[float]] = []
    y_rows: list[float] = []
    cutoffs: list[str] = []
    for row in rows:
        try:
            decoded = json.loads(row["feature_json"])
        except Exception:
            continue
        x_rows.append([_safe_float(decoded.get(name)) for name in accuracy.FEATURE_NAMES])
        y_rows.append(_safe_float(row.get("target_score"), 50.0))
        cutoffs.append(str(row.get("cutoff_timestamp") or ""))
    if not x_rows:
        return np.empty((0, len(accuracy.FEATURE_NAMES))), np.empty(0), []
    return np.asarray(x_rows, dtype=float), np.asarray(y_rows, dtype=float), cutoffs


def _fit_ridge(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std < 1e-6] = 1.0
    z = (x - mean) / std
    design = np.column_stack([np.ones(len(z)), z])
    regularizer = np.eye(design.shape[1], dtype=float) * accuracy.RIDGE_LAMBDA
    regularizer[0, 0] = 0.0
    try:
        beta = np.linalg.solve(design.T @ design + regularizer, design.T @ y)
    except np.linalg.LinAlgError:
        beta = np.linalg.lstsq(design.T @ design + regularizer, design.T @ y, rcond=None)[0]
    return mean, std, beta


def _ridge_predict(x: np.ndarray, mean: np.ndarray, std: np.ndarray, beta: np.ndarray) -> np.ndarray:
    z = (x - mean) / std
    design = np.column_stack([np.ones(len(z)), z])
    return np.clip(design @ beta, 0.0, 100.0)


def fit_model_v2(target: Any, symbol: str, timeframe: str) -> dict[str, Any]:
    samples, replay_count, max_id = _training_signature(target, symbol, timeframe)
    cache_key = (str(target.path), normalize_symbol(symbol), timeframe, samples, replay_count, max_id, ENGINE_VERSION)
    with _MODEL_LOCK:
        cached = _FAST_MODEL_CACHE.get(cache_key)
        if cached is not None:
            return dict(cached)
    payload: dict[str, Any] = {"version": accuracy.RUNTIME_VERSION, "engine_version": ENGINE_VERSION, "learned": False, "sample_count": samples, "replay_count": replay_count, "required_samples": accuracy.MIN_LEARNING_SAMPLES, "required_replays": accuracy.MIN_LEARNING_REPLAYS, "selector_type": "heuristic", "gbt_min_samples": GBT_MIN_SAMPLES, "gbt_min_replays": GBT_MIN_REPLAYS}
    if samples < accuracy.MIN_LEARNING_SAMPLES or replay_count < accuracy.MIN_LEARNING_REPLAYS:
        with _MODEL_LOCK:
            _FAST_MODEL_CACHE[cache_key] = dict(payload)
        return payload
    rows, replay_count, _ = accuracy._training_rows(target, symbol, timeframe)
    x, y, cutoffs = _decode_rows(rows)
    if len(x) < accuracy.MIN_LEARNING_SAMPLES:
        payload["sample_count"] = int(len(x))
        with _MODEL_LOCK:
            _FAST_MODEL_CACHE[cache_key] = dict(payload)
        return payload
    payload["learned"] = True
    payload["sample_count"] = int(len(x))
    payload["replay_count"] = int(replay_count)
    full_mean, full_std, full_beta = _fit_ridge(x, y)
    full_ridge_pred = _ridge_predict(x, full_mean, full_std, full_beta)
    payload.update({"selector_type": "ridge", "model": "ridge_path_quality_regressor", "mean": full_mean, "std": full_std, "beta": full_beta, "training_mae": round(float(np.mean(np.abs(full_ridge_pred - y))), 3), "feature_count": len(accuracy.FEATURE_NAMES), "gbt_ready": False})
    if len(x) >= GBT_MIN_SAMPLES and replay_count >= GBT_MIN_REPLAYS:
        unique_cutoffs = sorted(set(cutoffs))
        if len(unique_cutoffs) >= 10:
            split_at = max(1, int(len(unique_cutoffs) * (1.0 - GBT_VALIDATION_FRACTION)))
            train_cutoffs = set(unique_cutoffs[:split_at])
            train_mask = np.asarray([cutoff in train_cutoffs for cutoff in cutoffs], dtype=bool)
            valid_mask = ~train_mask
            if train_mask.sum() >= 200 and valid_mask.sum() >= 80:
                train_x, train_y = x[train_mask], y[train_mask]
                valid_x, valid_y = x[valid_mask], y[valid_mask]
                mean, std, beta = _fit_ridge(train_x, train_y)
                ridge_mae = float(np.mean(np.abs(_ridge_predict(valid_x, mean, std, beta) - valid_y)))
                challenger = GradientBoostedTrees().fit(train_x, train_y)
                gbt_mae = float(np.mean(np.abs(challenger.predict(valid_x) - valid_y)))
                payload.update({"gbt_ready": True, "ridge_validation_mae": round(ridge_mae, 3), "gbt_validation_mae": round(gbt_mae, 3), "validation_replays": len(unique_cutoffs) - split_at})
                if gbt_mae <= ridge_mae:
                    final_gbt = GradientBoostedTrees().fit(x, y)
                    handle = f"{normalize_symbol(symbol)}:{timeframe}:{max_id}:{len(x)}"
                    with _MODEL_LOCK:
                        _GBT_OBJECTS[handle] = final_gbt
                    payload.update({"selector_type": "gbt", "model": "gradient_boosted_regression_trees", "selector_handle": handle, "training_mae": round(float(np.mean(np.abs(final_gbt.predict(x) - y))), 3), "promoted_automatically": True})
    with _MODEL_LOCK:
        if len(_FAST_MODEL_CACHE) > 128:
            _FAST_MODEL_CACHE.clear()
        _FAST_MODEL_CACHE[cache_key] = dict(payload)
    return payload


def predict_quality_v2(model: dict[str, Any], features: dict[str, float]) -> float | None:
    if not model.get("learned"):
        return None
    vector = np.asarray([[ _safe_float(features.get(name)) for name in accuracy.FEATURE_NAMES ]], dtype=float)
    if model.get("selector_type") == "gbt":
        handle = str(model.get("selector_handle") or "")
        with _MODEL_LOCK:
            estimator = _GBT_OBJECTS.get(handle)
        if estimator is not None:
            return float(estimator.predict(vector)[0])
    mean = np.asarray(model["mean"], dtype=float)
    std = np.asarray(model["std"], dtype=float)
    beta = np.asarray(model["beta"], dtype=float)
    return float(_ridge_predict(vector, mean, std, beta)[0])


def matching_cache_v2(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    cached = _BASE_MATCHING_CACHE(*args, **kwargs)
    if not cached:
        return None
    revision = cached.get("revision") or {}
    market_context = revision.get("market_context") or {}
    ict_context = revision.get("ict_context") or market_context.get("ict") or {}
    cross = market_context.get("cross_market") or ict_context.get("cross_market") or {}
    selected = ((revision.get("path_ensemble") or {}).get("selected_path") or {})
    if cross.get("engine_version") != ENGINE_VERSION:
        return None
    if "selector_type" not in selected:
        return None
    return cached


_BASE_RANK_PATHS = accuracy.rank_paths_with_learning


def rank_paths_v2(paths: list[pd.DataFrame], *, base: float, timeframe: str, context: dict[str, Any]):
    selected_index, ranked, counts, vote_direction, vote_pct = _BASE_RANK_PATHS(paths, base=base, timeframe=timeframe, context=context)
    state = ict._RUNTIME_CONTEXT.get() or {}
    platform = state.get("platform")
    symbol = normalize_symbol(str(state.get("symbol") or "XAUUSD"))
    model = fit_model_v2(platform.store, symbol, timeframe) if platform is not None else {"selector_type": "heuristic", "sample_count": 0, "replay_count": 0}
    for item in ranked:
        item["selector_type"] = model.get("selector_type", "heuristic")
        item["selector_model"] = model.get("model")
        item["gbt_ready"] = bool(model.get("gbt_ready"))
        item["gbt_validation_mae"] = model.get("gbt_validation_mae")
        item["ridge_validation_mae"] = model.get("ridge_validation_mae")
        item["selector_engine_version"] = ENGINE_VERSION
    return selected_index, ranked, counts, vote_direction, vote_pct


accuracy._cross_market_context = cross_market_context_v2
accuracy._feature_vector = feature_vector_v2
accuracy._fit_model = fit_model_v2
accuracy._predict_quality = predict_quality_v2
accuracy.rank_paths_with_learning = rank_paths_v2
ict._rank_paths = rank_paths_v2
v2._matching_cache = matching_cache_v2
