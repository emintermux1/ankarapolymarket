from __future__ import annotations

from collections import defaultdict
from statistics import mean, pstdev

from src.data_sources.schemas import EnsembleForecast, ModelForecast


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


def model_family(model: str) -> str:
    lowered = model.lower()
    if "icon" in lowered:
        return "icon"
    if "ecmwf" in lowered or "aifs" in lowered:
        return "ecmwf"
    if "gfs" in lowered or "gefs" in lowered:
        return "gfs"
    return lowered


def normalize_family_balanced_weights(raw: dict[str, float]) -> dict[str, float]:
    grouped: dict[str, dict[str, float]] = defaultdict(dict)
    for model, value in raw.items():
        grouped[model_family(model)][model] = value
    family_scores = {
        family: mean(max(0.0, value) for value in members.values())
        for family, members in grouped.items()
    }
    family_weights = normalize_weights(family_scores)
    weights: dict[str, float] = {}
    for family, members in grouped.items():
        within_family = normalize_weights(members)
        for model, weight in within_family.items():
            weights[model] = weight * family_weights.get(family, 0.0)
    return weights


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
    return normalize_family_balanced_weights(raw)


def ensemble_sigma(ensembles: list[EnsembleForecast]) -> float | None:
    values = [
        value
        for ensemble in ensembles
        for value in ensemble.member_tmax_c
    ]
    if len(values) < 4:
        return None
    values = sorted(values)
    p10 = values[max(0, round((len(values) - 1) * 0.1))]
    p90 = values[min(len(values) - 1, round((len(values) - 1) * 0.9))]
    return max(0.1, (p90 - p10) / 2.56)


def historical_mae_sigma(historical_weights: dict[str, dict[str, float | None]]) -> float | None:
    values = []
    for history in historical_weights.values():
        candidates = [history.get("mae_7"), history.get("mae_14"), history.get("mae_30")]
        values.extend(float(value) for value in candidates if value is not None and value > 0)
    return mean(values) if values else None


def probability_sigma(
    *,
    deterministic_spread_c: float | None,
    ensemble_sigma_c: float | None,
    historical_sigma_c: float | None,
) -> float:
    return max(
        0.9,
        deterministic_spread_c or 0.0,
        ensemble_sigma_c or 0.0,
        historical_sigma_c or 0.0,
    )


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
