from __future__ import annotations

from statistics import pstdev

from src.data_sources.schemas import ModelForecast


def model_spread(forecasts: list[ModelForecast]) -> float | None:
    values = [forecast.tmax_c for forecast in forecasts if forecast.available and forecast.tmax_c is not None]
    if len(values) < 2:
        return None
    return float(pstdev(values))


def normalize_weights(raw: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, value) for value in raw.values())
    if total <= 0:
        count = len(raw) or 1
        return {key: 1.0 / count for key in raw}
    return {key: max(0.0, value) / total for key, value in raw.items()}


def calculate_model_weights(
    forecasts: list[ModelForecast],
    historical_weights: dict[str, dict[str, float | None]],
) -> dict[str, float]:
    available = [forecast for forecast in forecasts if forecast.available and forecast.tmax_c is not None]
    if not available:
        return {}

    raw: dict[str, float] = {}
    for forecast in available:
        history = historical_weights.get(forecast.model) or {}
        explicit = history.get("weight")
        if explicit is not None:
            raw[forecast.model] = float(explicit)
            continue
        mae_candidates = [history.get("mae_7"), history.get("mae_14"), history.get("mae_30")]
        mae_values = [float(value) for value in mae_candidates if value is not None and value > 0]
        raw[forecast.model] = 1.0 / (sum(mae_values) / len(mae_values)) if mae_values else 1.0
    return normalize_weights(raw)


def weighted_model_tmax(
    forecasts: list[ModelForecast],
    weights: dict[str, float],
    bias_offsets: dict[str, float],
) -> float | None:
    weighted_sum = 0.0
    used_weight = 0.0
    for forecast in forecasts:
        if not forecast.available or forecast.tmax_c is None:
            continue
        weight = weights.get(forecast.model, 0.0)
        bias = bias_offsets.get(forecast.model, 0.0)
        weighted_sum += weight * (forecast.tmax_c - bias)
        used_weight += weight
    if used_weight <= 0:
        return None
    return weighted_sum / used_weight

