from __future__ import annotations

import math
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd


ICT_VERSION = "ict_smc_v1"
_DIRECTIONAL = {"bullish", "bearish"}


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def _direction(value: float, threshold: float = 0.0) -> str:
    if value > threshold:
        return "bullish"
    if value < -threshold:
        return "bearish"
    return "sideways"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _frame(history: pd.DataFrame | Sequence[dict[str, Any]]) -> pd.DataFrame:
    data = history.copy() if isinstance(history, pd.DataFrame) else pd.DataFrame(history)
    required = ("timestamp", "open", "high", "low", "close")
    if any(column not in data.columns for column in required):
        return pd.DataFrame(columns=required)
    data = data.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume", "amount"):
        if column not in data.columns:
            data[column] = 0.0
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=["timestamp", "open", "high", "low", "close"])
    return data.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)


def _true_range(data: pd.DataFrame) -> pd.Series:
    previous_close = data["close"].shift(1)
    return pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _swings(data: pd.DataFrame, width: int = 2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    highs: list[dict[str, Any]] = []
    lows: list[dict[str, Any]] = []
    if len(data) < width * 2 + 1:
        return highs, lows
    high_values = data["high"].to_numpy(dtype=float)
    low_values = data["low"].to_numpy(dtype=float)
    for index in range(width, len(data) - width):
        high_window = high_values[index - width : index + width + 1]
        low_window = low_values[index - width : index + width + 1]
        if high_values[index] >= float(np.max(high_window)):
            highs.append(
                {
                    "index": index,
                    "timestamp": pd.Timestamp(data.iloc[index]["timestamp"]).isoformat(),
                    "price": float(high_values[index]),
                }
            )
        if low_values[index] <= float(np.min(low_window)):
            lows.append(
                {
                    "index": index,
                    "timestamp": pd.Timestamp(data.iloc[index]["timestamp"]).isoformat(),
                    "price": float(low_values[index]),
                }
            )
    return highs, lows


def _structure(data: pd.DataFrame, atr: float) -> dict[str, Any]:
    swing_highs, swing_lows = _swings(data)
    close = float(data.iloc[-1]["close"])
    threshold = max(atr * 0.08, abs(close) * 1e-6)

    latest_high = swing_highs[-1] if swing_highs else None
    latest_low = swing_lows[-1] if swing_lows else None
    previous_high = swing_highs[-2] if len(swing_highs) >= 2 else None
    previous_low = swing_lows[-2] if len(swing_lows) >= 2 else None

    sequence_bias = "sideways"
    if latest_high and previous_high and latest_low and previous_low:
        higher_high = latest_high["price"] > previous_high["price"] + threshold
        higher_low = latest_low["price"] > previous_low["price"] + threshold
        lower_high = latest_high["price"] < previous_high["price"] - threshold
        lower_low = latest_low["price"] < previous_low["price"] - threshold
        if higher_high and higher_low:
            sequence_bias = "bullish"
        elif lower_high and lower_low:
            sequence_bias = "bearish"

    bos = None
    if latest_high and close > latest_high["price"] + threshold:
        bos = "bullish"
    elif latest_low and close < latest_low["price"] - threshold:
        bos = "bearish"

    choch = None
    if bos in _DIRECTIONAL and sequence_bias in _DIRECTIONAL and bos != sequence_bias:
        choch = bos

    ema20 = data["close"].ewm(span=min(20, len(data)), adjust=False).mean()
    ema50 = data["close"].ewm(span=min(50, len(data)), adjust=False).mean()
    slope_window = min(8, len(data) - 1)
    slope = float(ema20.iloc[-1] - ema20.iloc[-1 - slope_window]) if slope_window else 0.0
    ema_bias = _direction(float(ema20.iloc[-1] - ema50.iloc[-1]) + slope * 0.35, max(atr * 0.10, threshold))

    bias = bos or (choch if choch else None) or (sequence_bias if sequence_bias in _DIRECTIONAL else ema_bias)
    if bias not in _DIRECTIONAL:
        bias = "sideways"

    evidence = 0.0
    if sequence_bias == bias:
        evidence += 28
    if bos == bias:
        evidence += 32
    if ema_bias == bias:
        evidence += 20
    if choch == bias:
        evidence += 20
    if bias == "sideways":
        evidence = max(20.0, 100.0 - evidence)

    state = (
        f"{bias}_choch"
        if choch == bias
        else f"{bias}_bos"
        if bos == bias
        else f"{bias}_structure"
        if bias in _DIRECTIONAL
        else "balanced"
    )
    return {
        "bias": bias,
        "state": state,
        "bos": bos,
        "choch": choch,
        "sequence_bias": sequence_bias,
        "ema_bias": ema_bias,
        "strength_pct": round(_clamp(evidence), 1),
        "last_swing_high": latest_high,
        "last_swing_low": latest_low,
        "swing_highs": swing_highs[-12:],
        "swing_lows": swing_lows[-12:],
    }


def _equal_level(swings: Sequence[dict[str, Any]], tolerance: float, side: str) -> dict[str, Any] | None:
    if len(swings) < 2:
        return None
    candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    recent = list(swings)[-10:]
    for first_index, first in enumerate(recent):
        for second in recent[first_index + 1 :]:
            distance = abs(float(first["price"]) - float(second["price"]))
            if distance <= tolerance:
                candidates.append((distance, first, second))
    if not candidates:
        return None
    _, first, second = min(candidates, key=lambda row: row[0])
    return {
        "type": "equal_highs" if side == "buy_side" else "equal_lows",
        "side": side,
        "price": round((float(first["price"]) + float(second["price"])) / 2, 8),
        "timestamps": [first["timestamp"], second["timestamp"]],
    }


def _previous_period_levels(data: pd.DataFrame) -> list[dict[str, Any]]:
    timestamp = pd.Timestamp(data.iloc[-1]["timestamp"])
    levels: list[dict[str, Any]] = []

    day = timestamp.floor("D")
    previous_day = data[(data["timestamp"] >= day - pd.Timedelta(days=1)) & (data["timestamp"] < day)]
    if not previous_day.empty:
        levels.extend(
            [
                {"type": "previous_day_high", "side": "buy_side", "price": float(previous_day["high"].max())},
                {"type": "previous_day_low", "side": "sell_side", "price": float(previous_day["low"].min())},
            ]
        )

    week = timestamp.floor("D") - pd.Timedelta(days=timestamp.weekday())
    previous_week = data[(data["timestamp"] >= week - pd.Timedelta(days=7)) & (data["timestamp"] < week)]
    if not previous_week.empty:
        levels.extend(
            [
                {"type": "previous_week_high", "side": "buy_side", "price": float(previous_week["high"].max())},
                {"type": "previous_week_low", "side": "sell_side", "price": float(previous_week["low"].min())},
            ]
        )
    return levels


def _session_name(timestamp: pd.Timestamp) -> tuple[str, bool]:
    hour = timestamp.hour + timestamp.minute / 60
    if 0 <= hour < 7:
        name = "asian"
    elif 7 <= hour < 12:
        name = "london"
    elif 12 <= hour < 21:
        name = "new_york"
    else:
        name = "after_hours"
    killzone = (7 <= hour < 10) or (12 <= hour < 15)
    return name, killzone


def _session_levels(data: pd.DataFrame) -> list[dict[str, Any]]:
    timestamp = pd.Timestamp(data.iloc[-1]["timestamp"])
    day_start = timestamp.floor("D")
    windows = [
        ("asian", 0, 7),
        ("london", 7, 12),
        ("new_york", 12, 21),
    ]
    levels: list[dict[str, Any]] = []
    for name, start_hour, end_hour in windows:
        start = day_start + pd.Timedelta(hours=start_hour)
        end = day_start + pd.Timedelta(hours=end_hour)
        sample = data[(data["timestamp"] >= start) & (data["timestamp"] < min(end, timestamp))]
        if sample.empty:
            continue
        levels.extend(
            [
                {"type": f"{name}_high", "side": "buy_side", "price": float(sample["high"].max())},
                {"type": f"{name}_low", "side": "sell_side", "price": float(sample["low"].min())},
            ]
        )
    return levels


def _liquidity(data: pd.DataFrame, structure: dict[str, Any], atr: float) -> dict[str, Any]:
    price = float(data.iloc[-1]["close"])
    tolerance = max(atr * 0.15, abs(price) * 1e-6)
    levels = _previous_period_levels(data) + _session_levels(data)
    equal_highs = _equal_level(structure["swing_highs"], tolerance, "buy_side")
    equal_lows = _equal_level(structure["swing_lows"], tolerance, "sell_side")
    if equal_highs:
        levels.append(equal_highs)
    if equal_lows:
        levels.append(equal_lows)
    if structure.get("last_swing_high"):
        levels.append(
            {
                "type": "swing_high",
                "side": "buy_side",
                "price": float(structure["last_swing_high"]["price"]),
            }
        )
    if structure.get("last_swing_low"):
        levels.append(
            {
                "type": "swing_low",
                "side": "sell_side",
                "price": float(structure["last_swing_low"]["price"]),
            }
        )

    deduplicated: list[dict[str, Any]] = []
    for level in levels:
        if not math.isfinite(_safe_float(level.get("price"), math.nan)):
            continue
        if any(
            level["side"] == existing["side"]
            and abs(float(level["price"]) - float(existing["price"])) <= tolerance * 0.25
            for existing in deduplicated
        ):
            continue
        deduplicated.append(level)

    current = data.iloc[-1]
    buy_candidates = [level for level in deduplicated if level["side"] == "buy_side" and float(level["price"]) >= price - tolerance]
    sell_candidates = [level for level in deduplicated if level["side"] == "sell_side" and float(level["price"]) <= price + tolerance]
    buy_candidates.sort(key=lambda level: abs(float(level["price"]) - price))
    sell_candidates.sort(key=lambda level: abs(float(level["price"]) - price))

    sweep = None
    for level in buy_candidates[:4]:
        level_price = float(level["price"])
        if float(current["high"]) > level_price + tolerance * 0.15 and float(current["close"]) < level_price:
            sweep = {"side": "buy_side", "direction": "bearish", **level}
            break
    if sweep is None:
        for level in sell_candidates[:4]:
            level_price = float(level["price"])
            if float(current["low"]) < level_price - tolerance * 0.15 and float(current["close"]) > level_price:
                sweep = {"side": "sell_side", "direction": "bullish", **level}
                break

    bias = structure.get("bias")
    directional_pool = buy_candidates if bias == "bullish" else sell_candidates if bias == "bearish" else []
    draw = directional_pool[0] if directional_pool else (buy_candidates[0] if buy_candidates else sell_candidates[0] if sell_candidates else None)

    return {
        "levels": deduplicated[-20:],
        "buy_side": buy_candidates[:6],
        "sell_side": sell_candidates[:6],
        "sweep": sweep,
        "draw": draw,
    }


def _fvg_zones(data: pd.DataFrame, atr: float) -> dict[str, Any]:
    bullish: list[dict[str, Any]] = []
    bearish: list[dict[str, Any]] = []
    minimum_gap = max(atr * 0.04, abs(float(data.iloc[-1]["close"])) * 1e-6)
    for index in range(2, len(data)):
        first = data.iloc[index - 2]
        third = data.iloc[index]
        if float(third["low"]) > float(first["high"]) + minimum_gap:
            zone = {
                "direction": "bullish",
                "low": float(first["high"]),
                "high": float(third["low"]),
                "created_at": pd.Timestamp(third["timestamp"]).isoformat(),
                "index": index,
            }
            subsequent = data.iloc[index + 1 :]
            zone["active"] = subsequent.empty or float(subsequent["low"].min()) > zone["low"]
            bullish.append(zone)
        if float(third["high"]) < float(first["low"]) - minimum_gap:
            zone = {
                "direction": "bearish",
                "low": float(third["high"]),
                "high": float(first["low"]),
                "created_at": pd.Timestamp(third["timestamp"]).isoformat(),
                "index": index,
            }
            subsequent = data.iloc[index + 1 :]
            zone["active"] = subsequent.empty or float(subsequent["high"].max()) < zone["high"]
            bearish.append(zone)

    price = float(data.iloc[-1]["close"])
    active_bullish = [zone for zone in bullish if zone["active"]]
    active_bearish = [zone for zone in bearish if zone["active"]]
    active_bullish.sort(key=lambda zone: abs((zone["low"] + zone["high"]) / 2 - price))
    active_bearish.sort(key=lambda zone: abs((zone["low"] + zone["high"]) / 2 - price))
    return {
        "bullish": active_bullish[:5],
        "bearish": active_bearish[:5],
        "nearest_bullish": active_bullish[0] if active_bullish else None,
        "nearest_bearish": active_bearish[0] if active_bearish else None,
        "active_count": len(active_bullish) + len(active_bearish),
    }


def _displacement(data: pd.DataFrame, atr: float) -> dict[str, Any]:
    current = data.iloc[-1]
    body = float(current["close"] - current["open"])
    candle_range = max(float(current["high"] - current["low"]), 1e-12)
    body_ratio = abs(body) / candle_range
    range_atr = candle_range / max(atr, 1e-12)
    direction = _direction(body, atr * 0.05)
    score = _clamp(body_ratio * 45 + min(range_atr, 2.5) / 2.5 * 45 + (10 if direction in _DIRECTIONAL else 0))
    return {
        "active": score >= 62 and direction in _DIRECTIONAL,
        "direction": direction,
        "score_pct": round(score, 1),
        "body_ratio_pct": round(body_ratio * 100, 1),
        "range_atr": round(range_atr, 3),
    }


def _order_block(data: pd.DataFrame, displacement: dict[str, Any], atr: float) -> dict[str, Any] | None:
    direction = displacement.get("direction")
    if direction not in _DIRECTIONAL:
        return None
    search = data.iloc[max(0, len(data) - 12) : -1]
    if search.empty:
        return None
    if direction == "bullish":
        candidates = search[search["close"] < search["open"]]
    else:
        candidates = search[search["close"] > search["open"]]
    if candidates.empty:
        return None
    candle = candidates.iloc[-1]
    low = float(min(candle["open"], candle["close"], candle["low"]))
    high = float(max(candle["open"], candle["close"], candle["high"]))
    if high - low > atr * 2.5:
        return None
    return {
        "direction": direction,
        "low": low,
        "high": high,
        "timestamp": pd.Timestamp(candle["timestamp"]).isoformat(),
    }


def _dealing_range(data: pd.DataFrame, structure: dict[str, Any]) -> dict[str, Any]:
    recent = data.tail(min(80, len(data)))
    low = float(recent["low"].min())
    high = float(recent["high"].max())
    if structure.get("last_swing_low"):
        low = min(low, float(structure["last_swing_low"]["price"]))
    if structure.get("last_swing_high"):
        high = max(high, float(structure["last_swing_high"]["price"]))
    width = max(high - low, 1e-12)
    price = float(data.iloc[-1]["close"])
    position = _clamp((price - low) / width * 100)
    zone = "discount" if position < 45 else "premium" if position > 55 else "equilibrium"
    return {
        "low": low,
        "high": high,
        "equilibrium": (low + high) / 2,
        "position_pct": round(position, 1),
        "zone": zone,
    }


def _event_context(
    events: Iterable[dict[str, Any]] | None,
    timestamp: pd.Timestamp,
    currencies: Sequence[str],
    blackout_minutes: int = 30,
) -> dict[str, Any]:
    relevant: list[dict[str, Any]] = []
    for event in events or []:
        impact = str(event.get("impact") or "").lower()
        currency = str(event.get("currency") or "").upper()
        if impact != "high" or (currencies and currency not in currencies):
            continue
        try:
            starts_at = pd.Timestamp(event["starts_at"])
            starts_at = starts_at.tz_localize("UTC") if starts_at.tzinfo is None else starts_at.tz_convert("UTC")
        except Exception:
            continue
        minutes = (starts_at - timestamp).total_seconds() / 60
        if abs(minutes) <= 120:
            relevant.append(
                {
                    "title": event.get("title") or "High-impact event",
                    "currency": currency,
                    "starts_at": starts_at.isoformat(),
                    "minutes": round(minutes, 1),
                }
            )
    relevant.sort(key=lambda item: abs(float(item["minutes"])))
    nearest = relevant[0] if relevant else None
    blocked = bool(nearest and abs(float(nearest["minutes"])) <= blackout_minutes)
    return {
        "blocked": blocked,
        "nearest": nearest,
        "blackout_minutes": blackout_minutes,
    }


def symbol_currencies(symbol: str) -> tuple[str, ...]:
    canonical = str(symbol).upper()
    if len(canonical) == 6 and canonical.isalpha():
        return canonical[:3], canonical[3:]
    if canonical in {"XAUUSD", "XAGUSD", "NAS100", "SPX500"}:
        return ("USD",)
    return ()


def context_model(context: dict[str, Any]) -> dict[str, Any]:
    structure = context.get("structure") or {}
    liquidity = context.get("liquidity") or {}
    displacement = context.get("displacement") or {}
    fvg = context.get("fair_value_gaps") or {}
    order_block = context.get("order_block") or {}
    dealing_range = context.get("dealing_range") or {}
    event = context.get("event_risk") or {}
    session = context.get("session") or {}

    bullish = 0.0
    bearish = 0.0
    no_trade = 0.4

    bias = structure.get("bias")
    structure_weight = _safe_float(structure.get("strength_pct")) / 100
    if bias == "bullish":
        bullish += 1.4 * structure_weight
    elif bias == "bearish":
        bearish += 1.4 * structure_weight
    else:
        no_trade += 0.8

    sweep = liquidity.get("sweep") or {}
    if sweep.get("direction") == "bullish":
        bullish += 1.0
    elif sweep.get("direction") == "bearish":
        bearish += 1.0

    displacement_direction = displacement.get("direction")
    displacement_weight = _safe_float(displacement.get("score_pct")) / 100
    if displacement_direction == "bullish":
        bullish += 0.9 * displacement_weight
    elif displacement_direction == "bearish":
        bearish += 0.9 * displacement_weight

    if fvg.get("nearest_bullish"):
        bullish += 0.25
    if fvg.get("nearest_bearish"):
        bearish += 0.25
    if order_block.get("direction") == "bullish":
        bullish += 0.35
    elif order_block.get("direction") == "bearish":
        bearish += 0.35

    zone = dealing_range.get("zone")
    if zone == "discount":
        bullish += 0.35
    elif zone == "premium":
        bearish += 0.35

    if session.get("killzone"):
        dominant = bullish if bullish >= bearish else bearish
        if dominant > 0:
            if bullish >= bearish:
                bullish += 0.15
            else:
                bearish += 0.15

    if event.get("blocked"):
        no_trade += 2.5
    if abs(bullish - bearish) < 0.35:
        no_trade += 0.75

    logits = np.asarray([bullish, bearish, no_trade], dtype=float)
    logits -= float(np.max(logits))
    probabilities = np.exp(logits)
    probabilities /= float(probabilities.sum())
    labels = ("bullish", "bearish", "no_trade")
    values = {label: round(float(probability) * 100, 1) for label, probability in zip(labels, probabilities)}
    dominant = max(values, key=values.get)
    return {
        "bullish_probability_pct": values["bullish"],
        "bearish_probability_pct": values["bearish"],
        "no_trade_probability_pct": values["no_trade"],
        "dominant": dominant,
        "raw_scores": {
            "bullish": round(bullish, 3),
            "bearish": round(bearish, 3),
            "no_trade": round(no_trade, 3),
        },
    }


def analyze_ict(
    history: pd.DataFrame | Sequence[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    events: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data = _frame(history)
    if len(data) < 12:
        return {
            "version": ICT_VERSION,
            "symbol": str(symbol).upper(),
            "timeframe": timeframe,
            "available": False,
            "structure": {"bias": "sideways", "state": "insufficient_history", "strength_pct": 0.0},
            "setup": {"state": "waiting", "quality_pct": 0.0, "bias": "sideways"},
            "context_signature": f"{str(symbol).upper()}|{timeframe}|insufficient",
        }

    true_range = _true_range(data)
    atr = max(float(true_range.tail(min(14, len(true_range))).mean()), 1e-12)
    structure = _structure(data, atr)
    liquidity = _liquidity(data, structure, atr)
    fvg = _fvg_zones(data, atr)
    displacement = _displacement(data, atr)
    order_block = _order_block(data, displacement, atr)
    dealing_range = _dealing_range(data, structure)
    timestamp = pd.Timestamp(data.iloc[-1]["timestamp"])
    session_name, killzone = _session_name(timestamp)
    session = {
        "name": session_name,
        "killzone": killzone,
        "timestamp": timestamp.isoformat(),
        "utc_hour": round(timestamp.hour + timestamp.minute / 60, 2),
        "proxy": True,
    }
    event_risk = _event_context(events, timestamp, symbol_currencies(symbol))

    evidence: list[str] = []
    bullish_score = 0.0
    bearish_score = 0.0
    if structure["bias"] == "bullish":
        bullish_score += structure["strength_pct"] * 0.35
        evidence.append(structure["state"])
    elif structure["bias"] == "bearish":
        bearish_score += structure["strength_pct"] * 0.35
        evidence.append(structure["state"])

    sweep = liquidity.get("sweep")
    if sweep:
        evidence.append(f"{sweep['side']}_sweep")
        if sweep.get("direction") == "bullish":
            bullish_score += 25
        elif sweep.get("direction") == "bearish":
            bearish_score += 25

    if displacement.get("active"):
        evidence.append(f"{displacement['direction']}_displacement")
        if displacement["direction"] == "bullish":
            bullish_score += displacement["score_pct"] * 0.25
        elif displacement["direction"] == "bearish":
            bearish_score += displacement["score_pct"] * 0.25

    if order_block:
        evidence.append(f"{order_block['direction']}_order_block")
        if order_block["direction"] == "bullish":
            bullish_score += 8
        else:
            bearish_score += 8

    if dealing_range["zone"] == "discount":
        bullish_score += 8
    elif dealing_range["zone"] == "premium":
        bearish_score += 8

    setup_bias = "bullish" if bullish_score > bearish_score + 8 else "bearish" if bearish_score > bullish_score + 8 else "sideways"
    quality = _clamp(max(bullish_score, bearish_score))
    state = (
        "blocked_event"
        if event_risk["blocked"]
        else "ready"
        if setup_bias in _DIRECTIONAL and quality >= 58 and (sweep or displacement.get("active"))
        else "developing"
        if setup_bias in _DIRECTIONAL and quality >= 38
        else "waiting"
    )
    setup = {
        "state": state,
        "bias": setup_bias,
        "quality_pct": round(quality, 1),
        "evidence": evidence[-6:],
        "trigger": (
            f"{sweep['side']}_sweep"
            if sweep
            else f"{displacement['direction']}_displacement"
            if displacement.get("active")
            else None
        ),
    }

    context = {
        "version": ICT_VERSION,
        "available": True,
        "symbol": str(symbol).upper(),
        "timeframe": timeframe,
        "atr": atr,
        "structure": structure,
        "liquidity": liquidity,
        "fair_value_gaps": fvg,
        "order_block": order_block,
        "dealing_range": dealing_range,
        "displacement": displacement,
        "session": session,
        "event_risk": event_risk,
        "setup": setup,
    }
    model = context_model(context)
    context["context_model"] = model
    signature_parts = [
        str(symbol).upper(),
        timeframe,
        structure.get("state", "unknown"),
        setup.get("state", "waiting"),
        setup.get("bias", "sideways"),
        dealing_range.get("zone", "unknown"),
        session.get("name", "unknown"),
        "event" if event_risk.get("blocked") else "clear",
    ]
    context["context_signature"] = "|".join(signature_parts)
    draw = liquidity.get("draw")
    context["compact"] = {
        "structure": str(structure.get("state", "unknown")).replace("_", " ").upper(),
        "liquidity": (
            f"{str(sweep.get('side')).replace('_', ' ').upper()} SWEPT"
            if sweep
            else f"DRAW → {str(draw.get('type')).replace('_', ' ').upper()}"
            if draw
            else "NO CLEAR DRAW"
        ),
        "session": f"{session_name.replace('_', ' ').upper()}{' KILLZONE' if killzone else ''}",
        "setup": f"{setup.get('state', 'waiting').upper()} · {setup.get('bias', 'sideways').upper()}",
    }
    return context


def zone_touched(path: pd.DataFrame, zone: dict[str, Any] | None, limit: int) -> bool:
    if not zone or path.empty:
        return False
    sample = path.head(max(1, min(limit, len(path))))
    low = _safe_float(zone.get("low"), math.nan)
    high = _safe_float(zone.get("high"), math.nan)
    if not math.isfinite(low) or not math.isfinite(high):
        return False
    return bool(((sample["low"].astype(float) <= high) & (sample["high"].astype(float) >= low)).any())
