from __future__ import annotations

import contextvars
import json
import math
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import numpy as np
import pandas as pd

from . import ict_runtime as ict
from . import intelligence_v2 as v2
from .market import normalize_symbol
from .platform import PlatformStore, utc_now_iso
from .service import store


RUNTIME_VERSION = "learned_cross_market_v1"
MIN_LEARNING_SAMPLES = 80
MIN_LEARNING_REPLAYS = 8
FULL_DIRECTION_UNLOCK_REPLAYS = 20
MAX_TRAINING_ROWS = 6000
RIDGE_LAMBDA = 2.0

PEER_MAP: dict[str, tuple[str, ...]] = {
    "XAUUSD": ("XAGUSD", "EURUSD", "USDJPY"),
    "XAGUSD": ("XAUUSD", "EURUSD"),
    "EURUSD": ("XAUUSD", "USDJPY"),
    "USDJPY": ("EURUSD", "XAUUSD"),
    "NAS100": ("SPX500",),
    "SPX500": ("NAS100",),
}

FEATURE_NAMES = (
    "path_support",
    "median_proximity",
    "structure_alignment",
    "liquidity_objective",
    "fvg_order_block",
    "premium_discount",
    "displacement",
    "volatility_plausibility",
    "cross_alignment",
    "cross_signal",
    "cross_strength",
    "trend_strength",
    "setup_quality",
    "path_direction",
    "move_atr",
    "range_atr",
    "turn_rate",
    "regime_trend",
    "regime_breakout",
    "regime_volatile",
)

_BASE_MARKET_CONTEXT = v2._market_context
_BASE_RANK_PATHS = ict._rank_paths
_REPLAY_CAPTURE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "traid_path_learning_capture",
    default=None,
)
_MODEL_LOCK = threading.RLock()
_MODEL_CACHE: dict[tuple[str, str, str, int, int], dict[str, Any]] = {}


def _ensure_schema(target: PlatformStore) -> None:
    with target._lock, target.connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS path_learning_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                cutoff_timestamp TEXT NOT NULL,
                path_index INTEGER NOT NULL,
                feature_json TEXT NOT NULL,
                target_score REAL NOT NULL,
                realized_candles INTEGER NOT NULL,
                selected_by_runtime INTEGER NOT NULL DEFAULT 0,
                UNIQUE(symbol,timeframe,cutoff_timestamp,path_index)
            );
            CREATE INDEX IF NOT EXISTS idx_path_learning_market
            ON path_learning_samples(symbol,timeframe,id DESC);
            """
        )


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _clean_candles(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    clean = frame.copy()
    clean["timestamp"] = pd.to_datetime(clean["timestamp"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close"):
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    return (
        clean.dropna(subset=["timestamp", "open", "high", "low", "close"])
        .sort_values("timestamp")
        .drop_duplicates("timestamp", keep="last")
        .reset_index(drop=True)
    )


def _cross_market_context(frame: pd.DataFrame, state: dict[str, Any]) -> dict[str, Any]:
    platform = state.get("platform")
    symbol = normalize_symbol(str(state.get("symbol") or "XAUUSD"))
    timeframe = str(state.get("timeframe") or "5m")
    peers = PEER_MAP.get(symbol, ())
    if platform is None or not peers:
        return {
            "version": RUNTIME_VERSION,
            "available": False,
            "bias": "neutral",
            "signal": 0.0,
            "strength_pct": 0.0,
            "peers": [],
        }

    base_frame = _clean_candles(frame)
    if len(base_frame) < 12:
        return {
            "version": RUNTIME_VERSION,
            "available": False,
            "bias": "neutral",
            "signal": 0.0,
            "strength_pct": 0.0,
            "peers": [],
        }

    cache = state.setdefault("_cross_market_cache", {})
    cache_key = (symbol, timeframe, str(base_frame["timestamp"].iloc[-1]))
    if cache_key in cache:
        return dict(cache[cache_key])

    base = base_frame[["timestamp", "close"]].rename(columns={"close": "base_close"})
    details: list[dict[str, Any]] = []
    weighted_signal = 0.0
    weight_total = 0.0

    for peer in peers:
        try:
            peer_frame = platform.engine.provider.get_candles(peer, timeframe, 160)
            peer_clean = _clean_candles(peer_frame)
            merged = base.merge(
                peer_clean[["timestamp", "close"]].rename(columns={"close": "peer_close"}),
                on="timestamp",
                how="inner",
            ).tail(120)
            if len(merged) < 12:
                continue

            base_returns = merged["base_close"].pct_change()
            peer_returns = merged["peer_close"].pct_change()
            valid = pd.concat([base_returns, peer_returns], axis=1).dropna().tail(60)
            if len(valid) < 8:
                continue
            correlation = _safe_float(valid.iloc[:, 0].corr(valid.iloc[:, 1]))
            if abs(correlation) < 0.10:
                continue

            lookback = min(3, len(merged) - 1)
            peer_start = _safe_float(merged["peer_close"].iloc[-1 - lookback])
            peer_end = _safe_float(merged["peer_close"].iloc[-1])
            if abs(peer_start) < 1e-12:
                continue
            peer_move_pct = (peer_end - peer_start) / abs(peer_start) * 100
            peer_vol_pct = max(
                _safe_float(peer_returns.tail(30).std()) * 100 * math.sqrt(max(lookback, 1)),
                0.002,
            )
            move_z = math.tanh(peer_move_pct / max(peer_vol_pct * 1.5, 0.002))
            contribution = math.copysign(1.0, correlation) * move_z
            relevance = abs(correlation)

            weighted_signal += contribution * relevance
            weight_total += relevance
            details.append(
                {
                    "symbol": peer,
                    "correlation": round(correlation, 4),
                    "move_pct": round(peer_move_pct, 4),
                    "normalized_move": round(move_z, 4),
                    "contribution": round(contribution, 4),
                    "relevance_pct": round(relevance * 100, 1),
                    "samples": int(len(valid)),
                }
            )
        except Exception as exc:
            details.append({"symbol": peer, "available": False, "detail": str(exc)})

    signal = weighted_signal / weight_total if weight_total > 0 else 0.0
    signal = max(-1.0, min(1.0, signal))
    bias = "bullish" if signal >= 0.15 else "bearish" if signal <= -0.15 else "neutral"

    payload = {
        "version": RUNTIME_VERSION,
        "available": weight_total > 0,
        "bias": bias,
        "signal": round(signal, 4),
        "strength_pct": round(abs(signal) * 100, 1),
        "peer_count": sum(1 for item in details if item.get("available", True)),
        "peers": details,
        "method": "rolling_return_correlation_x_normalized_peer_momentum",
        "completed_candles_only": True,
    }
    cache[cache_key] = dict(payload)
    return payload


def market_context_with_cross_market(frame: pd.DataFrame) -> dict[str, Any]:
    context = dict(_BASE_MARKET_CONTEXT(frame))
    state = ict._RUNTIME_CONTEXT.get() or {}
    try:
        cross = _cross_market_context(frame, state)
    except Exception as exc:
        cross = {
            "version": RUNTIME_VERSION,
            "available": False,
            "bias": "neutral",
            "signal": 0.0,
            "strength_pct": 0.0,
            "peers": [],
            "detail": str(exc),
        }

    context["cross_market"] = cross
    ict_context = dict(context.get("ict") or {})
    if ict_context:
        ict_context["cross_market"] = cross
        context["ict"] = ict_context
    return context


def _cross_alignment(direction: str, cross: dict[str, Any]) -> float:
    bias = str(cross.get("bias") or "neutral")
    strength = _safe_float(cross.get("strength_pct")) / 100
    if bias not in {"bullish", "bearish"} or strength <= 0:
        return 50.0
    raw = 100.0 if direction == bias else 55.0 if direction == "sideways" else 0.0
    return _clamp(50.0 + (raw - 50.0) * min(strength, 1.0))


def _path_shape_features(path: pd.DataFrame, *, base: float, atr: float) -> dict[str, float]:
    clean = _clean_candles(path)
    if clean.empty:
        return {"path_direction": 0.0, "move_atr": 0.0, "range_atr": 0.0, "turn_rate": 0.0}

    end = _safe_float(clean["close"].iloc[-1], base)
    move = end - base
    direction = 1.0 if move > 0 else -1.0 if move < 0 else 0.0
    scale = max(atr, abs(base) * 1e-6, 1e-9)
    move_atr = max(-5.0, min(5.0, move / scale)) / 5.0
    path_range = _safe_float(clean["high"].max() - clean["low"].min())
    range_atr = max(0.0, min(10.0, path_range / scale)) / 10.0

    diffs = clean["close"].diff().dropna().to_numpy(dtype=float)
    if len(diffs) < 2:
        turn_rate = 0.0
    else:
        signs = np.sign(diffs)
        turns = np.sum(signs[1:] * signs[:-1] < 0)
        turn_rate = float(turns / max(len(signs) - 1, 1))

    return {
        "path_direction": direction,
        "move_atr": float(move_atr),
        "range_atr": float(range_atr),
        "turn_rate": float(turn_rate),
    }


def _feature_vector(
    item: dict[str, Any],
    path: pd.DataFrame,
    *,
    base: float,
    context: dict[str, Any],
    cross_alignment: float,
) -> dict[str, float]:
    components = item.get("components") or {}
    cross = context.get("cross_market") or ((context.get("ict") or {}).get("cross_market")) or {}
    ict_context = context.get("ict") or {}
    trend = context.get("trend") or {}
    setup = ict_context.get("setup") or {}
    regime = str(context.get("regime") or "").lower()
    atr = _safe_float(ict_context.get("atr") or (context.get("volatility") or {}).get("atr"))
    shape = _path_shape_features(path, base=base, atr=atr)

    return {
        "path_support": _safe_float(components.get("path_support_pct"), 50.0) / 100,
        "median_proximity": _safe_float(components.get("median_proximity_pct"), 50.0) / 100,
        "structure_alignment": _safe_float(components.get("structure_alignment_pct"), 50.0) / 100,
        "liquidity_objective": _safe_float(components.get("liquidity_objective_pct"), 50.0) / 100,
        "fvg_order_block": _safe_float(components.get("fvg_order_block_pct"), 50.0) / 100,
        "premium_discount": _safe_float(components.get("premium_discount_pct"), 50.0) / 100,
        "displacement": _safe_float(components.get("displacement_pct"), 50.0) / 100,
        "volatility_plausibility": _safe_float(components.get("volatility_plausibility_pct"), 50.0) / 100,
        "cross_alignment": cross_alignment / 100,
        "cross_signal": max(-1.0, min(1.0, _safe_float(cross.get("signal")))),
        "cross_strength": _safe_float(cross.get("strength_pct")) / 100,
        "trend_strength": _safe_float(trend.get("strength_pct")) / 100,
        "setup_quality": _safe_float(setup.get("quality_pct")) / 100,
        **shape,
        "regime_trend": 1.0 if "trend" in regime else 0.0,
        "regime_breakout": 1.0 if "breakout" in regime else 0.0,
        "regime_volatile": 1.0 if "volatile" in regime else 0.0,
    }


def _training_rows(target: PlatformStore, symbol: str, timeframe: str) -> tuple[list[dict[str, Any]], int, int]:
    _ensure_schema(target)
    canonical = normalize_symbol(symbol)
    with target.connection() as connection:
        stats = connection.execute(
            """
            SELECT COUNT(*) AS samples,
                   COUNT(DISTINCT cutoff_timestamp) AS replays,
                   COALESCE(MAX(id),0) AS max_id
            FROM path_learning_samples
            WHERE symbol=? AND timeframe=?
            """,
            (canonical, timeframe),
        ).fetchone()
        rows = connection.execute(
            """
            SELECT feature_json,target_score,cutoff_timestamp
            FROM path_learning_samples
            WHERE symbol=? AND timeframe=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (canonical, timeframe, MAX_TRAINING_ROWS),
        ).fetchall()
    return [dict(row) for row in rows], int(stats["replays"] or 0), int(stats["max_id"] or 0)


def _fit_model(target: PlatformStore, symbol: str, timeframe: str) -> dict[str, Any]:
    rows, replay_count, max_id = _training_rows(target, symbol, timeframe)
    cache_key = (str(target.path), normalize_symbol(symbol), timeframe, len(rows), max_id)
    with _MODEL_LOCK:
        cached = _MODEL_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    payload: dict[str, Any] = {
        "version": RUNTIME_VERSION,
        "learned": False,
        "sample_count": len(rows),
        "replay_count": replay_count,
        "required_samples": MIN_LEARNING_SAMPLES,
        "required_replays": MIN_LEARNING_REPLAYS,
    }
    if len(rows) < MIN_LEARNING_SAMPLES or replay_count < MIN_LEARNING_REPLAYS:
        with _MODEL_LOCK:
            _MODEL_CACHE.clear()
            _MODEL_CACHE[cache_key] = dict(payload)
        return payload

    features: list[list[float]] = []
    targets: list[float] = []
    for row in rows:
        try:
            decoded = json.loads(row["feature_json"])
        except Exception:
            continue
        features.append([_safe_float(decoded.get(name)) for name in FEATURE_NAMES])
        targets.append(_safe_float(row["target_score"], 50.0))

    if len(features) < MIN_LEARNING_SAMPLES:
        payload["sample_count"] = len(features)
        with _MODEL_LOCK:
            _MODEL_CACHE.clear()
            _MODEL_CACHE[cache_key] = dict(payload)
        return payload

    matrix = np.asarray(features, dtype=float)
    target_values = np.asarray(targets, dtype=float)
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    std[std < 1e-6] = 1.0
    standardized = (matrix - mean) / std
    design = np.column_stack([np.ones(len(standardized)), standardized])
    regularizer = np.eye(design.shape[1], dtype=float) * RIDGE_LAMBDA
    regularizer[0, 0] = 0.0
    try:
        beta = np.linalg.solve(design.T @ design + regularizer, design.T @ target_values)
    except np.linalg.LinAlgError:
        beta = np.linalg.lstsq(design.T @ design + regularizer, design.T @ target_values, rcond=None)[0]

    fitted = design @ beta
    mae = float(np.mean(np.abs(fitted - target_values)))
    payload.update(
        {
            "learned": True,
            "sample_count": len(features),
            "replay_count": replay_count,
            "mean": mean,
            "std": std,
            "beta": beta,
            "training_mae": round(mae, 3),
            "feature_count": len(FEATURE_NAMES),
            "model": "ridge_path_quality_regressor",
        }
    )
    with _MODEL_LOCK:
        _MODEL_CACHE.clear()
        _MODEL_CACHE[cache_key] = dict(payload)
    return payload


def _predict_quality(model: dict[str, Any], features: dict[str, float]) -> float | None:
    if not model.get("learned"):
        return None
    vector = np.asarray([_safe_float(features.get(name)) for name in FEATURE_NAMES], dtype=float)
    mean = np.asarray(model["mean"], dtype=float)
    std = np.asarray(model["std"], dtype=float)
    beta = np.asarray(model["beta"], dtype=float)
    standardized = (vector - mean) / std
    design = np.concatenate([[1.0], standardized])
    return _clamp(float(design @ beta))


def _learning_weight(model: dict[str, Any]) -> float:
    if not model.get("learned"):
        return 0.0
    replays = int(model.get("replay_count") or 0)
    maturity = max(0.0, min(1.0, (replays - MIN_LEARNING_REPLAYS) / max(30 - MIN_LEARNING_REPLAYS, 1)))
    return 0.25 + maturity * 0.35


def rank_paths_with_learning(
    paths: list[pd.DataFrame],
    *,
    base: float,
    timeframe: str,
    context: dict[str, Any],
) -> tuple[int, list[dict[str, Any]], dict[str, int], str, float]:
    base_selected, ranked, counts, vote_direction, vote_pct = _BASE_RANK_PATHS(
        paths,
        base=base,
        timeframe=timeframe,
        context=context,
    )
    state = ict._RUNTIME_CONTEXT.get() or {}
    platform = state.get("platform")
    symbol = normalize_symbol(str(state.get("symbol") or "XAUUSD"))
    cross = context.get("cross_market") or ((context.get("ict") or {}).get("cross_market")) or {}
    model = _fit_model(platform.store, symbol, timeframe) if platform is not None else {
        "learned": False,
        "sample_count": 0,
        "replay_count": 0,
    }
    learned_weight = _learning_weight(model)
    cross_strength = _safe_float(cross.get("strength_pct")) / 100
    cross_weight = min(0.18, 0.08 + max(0.0, min(1.0, cross_strength)) * 0.10) if cross.get("available") else 0.0

    for item in ranked:
        index = int(item["index"])
        alignment = _cross_alignment(str(item.get("direction") or "sideways"), cross)
        heuristic = _safe_float(item.get("score_pct"), 50.0)
        features = _feature_vector(item, paths[index], base=base, context=context, cross_alignment=alignment)
        cross_adjusted = heuristic * (1.0 - cross_weight) + alignment * cross_weight
        learned_score = _predict_quality(model, features)
        final_score = cross_adjusted if learned_score is None else cross_adjusted * (1.0 - learned_weight) + learned_score * learned_weight
        item["heuristic_score_pct"] = round(heuristic, 2)
        item["cross_market_alignment_pct"] = round(alignment, 2)
        item["cross_market_weight_pct"] = round(cross_weight * 100, 1)
        item["learned_score_pct"] = round(learned_score, 2) if learned_score is not None else None
        item["learned_weight_pct"] = round(learned_weight * 100, 1)
        item["learning_samples"] = int(model.get("sample_count") or 0)
        item["learning_replays"] = int(model.get("replay_count") or 0)
        item["features"] = {name: round(float(features[name]), 6) for name in FEATURE_NAMES}
        item["score_pct"] = round(_clamp(final_score), 2)

    base_direction = str(ranked[base_selected].get("direction") or "sideways")
    replay_count = int(model.get("replay_count") or 0)
    if model.get("learned") and replay_count >= FULL_DIRECTION_UNLOCK_REPLAYS:
        eligible = ranked
        direction_policy = "learned_all_directions"
    else:
        eligible = [item for item in ranked if item.get("direction") == base_direction] or ranked
        direction_policy = "ict_direction_guard"

    selected = max(eligible, key=lambda item: _safe_float(item.get("score_pct")))
    selected_index = int(selected["index"])
    selected["selected_by"] = "learned_path_selector" if model.get("learned") else "cross_market_plus_ict" if cross.get("available") else "ict"
    selected["direction_policy"] = direction_policy

    capture = _REPLAY_CAPTURE.get()
    if capture is not None and normalize_symbol(str(capture.get("symbol"))) == symbol and str(capture.get("timeframe")) == timeframe:
        capture["base"] = float(base)
        capture["cross_market"] = cross
        capture["model"] = {key: value for key, value in model.items() if key not in {"mean", "std", "beta"}}
        capture["selected_index"] = selected_index
        capture["entries"] = [
            {
                "path_index": int(item["index"]),
                "path": _clean_candles(paths[int(item["index"])]).to_dict(orient="records"),
                "features": dict(item.get("features") or {}),
                "heuristic_score_pct": item.get("heuristic_score_pct"),
                "runtime_score_pct": item.get("score_pct"),
                "selected_by_runtime": int(item["index"]) == selected_index,
            }
            for item in ranked
        ]

    return selected_index, ranked, counts, vote_direction, vote_pct


@contextmanager
def capture_replay_candidates(symbol: str, timeframe: str) -> Iterator[dict[str, Any]]:
    payload: dict[str, Any] = {"symbol": normalize_symbol(symbol), "timeframe": timeframe, "entries": []}
    token = _REPLAY_CAPTURE.set(payload)
    try:
        yield payload
    finally:
        _REPLAY_CAPTURE.reset(token)


def _whole_path_score(path_rows: list[dict[str, Any]], actual: pd.DataFrame, *, base: float, atr: float) -> float | None:
    predicted = _clean_candles(pd.DataFrame(path_rows))
    observed = _clean_candles(actual)
    if predicted.empty or observed.empty:
        return None

    merged = predicted.merge(
        observed[["timestamp", "open", "high", "low", "close"]],
        on="timestamp",
        suffixes=("_pred", "_actual"),
        how="inner",
    )
    if len(merged) < 3:
        count = min(len(predicted), len(observed))
        if count < 3:
            return None
        predicted = predicted.iloc[:count].reset_index(drop=True)
        observed = observed.iloc[:count].reset_index(drop=True)
        merged = pd.DataFrame(
            {
                "close_pred": predicted["close"],
                "high_pred": predicted["high"],
                "low_pred": predicted["low"],
                "close_actual": observed["close"],
                "high_actual": observed["high"],
                "low_actual": observed["low"],
            }
        )

    scale = max(atr, abs(base) * 1e-6, 1e-9)
    pred_final = _safe_float(merged["close_pred"].iloc[-1], base)
    actual_final = _safe_float(merged["close_actual"].iloc[-1], base)
    pred_direction = 1 if pred_final > base else -1 if pred_final < base else 0
    actual_direction = 1 if actual_final > base else -1 if actual_final < base else 0
    direction_score = 100.0 if pred_direction == actual_direction else 0.0

    mean_close_atr = float(np.mean(np.abs(merged["close_pred"] - merged["close_actual"])) / scale)
    close_score = 100.0 * math.exp(-mean_close_atr)
    high_low_atr = float(
        np.mean((np.abs(merged["high_pred"] - merged["high_actual"]) + np.abs(merged["low_pred"] - merged["low_actual"])) / 2) / scale
    )
    high_low_score = 100.0 * math.exp(-high_low_atr)
    contained = (merged["close_actual"] >= merged["low_pred"]) & (merged["close_actual"] <= merged["high_pred"])
    containment_score = float(contained.mean() * 100)

    pred_diff = np.sign(np.diff(merged["close_pred"].to_numpy(dtype=float)))
    actual_diff = np.sign(np.diff(merged["close_actual"].to_numpy(dtype=float)))
    turning_score = float(np.mean(pred_diff == actual_diff) * 100) if len(pred_diff) else 50.0

    return _clamp(
        direction_score * 0.25
        + close_score * 0.35
        + containment_score * 0.15
        + high_low_score * 0.15
        + turning_score * 0.10
    )


def record_replay_outcome(
    target: PlatformStore,
    capture: dict[str, Any] | None,
    actual: pd.DataFrame,
    *,
    cutoff_timestamp: str,
    atr: float | None,
) -> dict[str, Any]:
    if not capture or not capture.get("entries"):
        return {"recorded": 0, "learned": False, "detail": "No candidate paths were captured."}
    observed = _clean_candles(actual)
    if len(observed) < 3:
        return {
            "recorded": 0,
            "learned": False,
            "detail": "At least 3 realized candles are required to train the path selector.",
        }

    base = _safe_float(capture.get("base"))
    scale = _safe_float(atr)
    if scale <= 0:
        previous_close = observed["close"].shift(1)
        true_range = pd.concat(
            [
                observed["high"] - observed["low"],
                (observed["high"] - previous_close).abs(),
                (observed["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        scale = max(_safe_float(true_range.mean()), abs(base) * 1e-6, 1e-9)

    _ensure_schema(target)
    canonical = normalize_symbol(str(capture.get("symbol")))
    timeframe = str(capture.get("timeframe"))
    rows_to_write: list[tuple[Any, ...]] = []
    for entry in capture.get("entries") or []:
        score = _whole_path_score(entry.get("path") or [], observed, base=base, atr=scale)
        features = entry.get("features") or {}
        if score is None or any(name not in features for name in FEATURE_NAMES):
            continue
        rows_to_write.append(
            (
                utc_now_iso(),
                canonical,
                timeframe,
                cutoff_timestamp,
                int(entry.get("path_index") or 0),
                json.dumps(features, separators=(",", ":")),
                float(score),
                int(len(observed)),
                1 if entry.get("selected_by_runtime") else 0,
            )
        )

    if rows_to_write:
        with target._lock, target.connection() as connection:
            connection.executemany(
                """
                INSERT INTO path_learning_samples(
                    created_at,symbol,timeframe,cutoff_timestamp,path_index,
                    feature_json,target_score,realized_candles,selected_by_runtime
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol,timeframe,cutoff_timestamp,path_index)
                DO UPDATE SET
                    created_at=excluded.created_at,
                    feature_json=excluded.feature_json,
                    target_score=excluded.target_score,
                    realized_candles=excluded.realized_candles,
                    selected_by_runtime=excluded.selected_by_runtime
                """,
                rows_to_write,
            )
        with _MODEL_LOCK:
            _MODEL_CACHE.clear()

    model = _fit_model(target, canonical, timeframe)
    return {
        "recorded": len(rows_to_write),
        "learned": bool(model.get("learned")),
        "sample_count": int(model.get("sample_count") or 0),
        "replay_count": int(model.get("replay_count") or 0),
        "required_samples": MIN_LEARNING_SAMPLES,
        "required_replays": MIN_LEARNING_REPLAYS,
        "training_mae": model.get("training_mae"),
        "model": model.get("model"),
        "runtime_version": RUNTIME_VERSION,
    }


_ensure_schema(store)
v2._market_context = market_context_with_cross_market
ict._rank_paths = rank_paths_with_learning
