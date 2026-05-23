from __future__ import annotations

from src.data_sources.schemas import MarketSnapshot, METARNormalized


def calculate_confidence(
    *,
    model_spread_c: float | None,
    available_models: int,
    expected_models: int,
    metar: METARNormalized | None,
    live_delta_c: float | None,
    cloud_uncertainty_pct: float | None,
    precip_spread_mm: float | None,
    has_taf: bool,
    has_history: bool,
    market: MarketSnapshot | None,
) -> tuple[int, dict[str, object]]:
    score = 58.0
    factors: dict[str, object] = {}

    if model_spread_c is None:
        score -= 12
        factors["model_spread"] = "unavailable"
    elif model_spread_c <= 0.8:
        score += 14
        factors["model_spread"] = "tight"
    elif model_spread_c <= 1.5:
        score += 7
        factors["model_spread"] = "moderate"
    elif model_spread_c <= 2.5:
        score -= 3
        factors["model_spread"] = "wide"
    else:
        score -= 16
        factors["model_spread"] = "very_wide"

    availability_ratio = available_models / max(1, expected_models)
    factors["model_availability"] = availability_ratio
    if availability_ratio >= 1:
        score += 8
    elif availability_ratio >= 0.66:
        score += 2
    else:
        score -= 12

    if metar is None:
        score -= 14
        factors["metar"] = "unavailable"
    elif metar.is_stale:
        score -= 14
        factors["metar"] = f"stale_{metar.age_minutes:.0f}m"
    else:
        score += 10
        factors["metar"] = f"fresh_{metar.age_minutes:.0f}m"

    if live_delta_c is not None:
        if abs(live_delta_c) <= 0.8:
            score += 7
            factors["live_alignment"] = "aligned"
        elif abs(live_delta_c) <= 1.8:
            factors["live_alignment"] = "slightly_off"
        else:
            score -= 9
            factors["live_alignment"] = "off_path"

    if cloud_uncertainty_pct is not None and cloud_uncertainty_pct > 35:
        score -= 6
        factors["cloud_uncertainty"] = "high"
    if precip_spread_mm is not None and precip_spread_mm > 4:
        score -= 6
        factors["rain_uncertainty"] = "high"

    if has_taf:
        score += 4
        factors["taf"] = "available"
    else:
        score -= 4
        factors["taf"] = "unavailable"

    if has_history:
        score += 5
        factors["backtest"] = "available"
    else:
        score -= 5
        factors["backtest"] = "not_enough_history"

    if market is not None and market.valid_for_target:
        liquidity = market.liquidity or 0
        if liquidity >= 10_000:
            score += 2
            factors["market_liquidity"] = "usable"
        else:
            factors["market_liquidity"] = "thin"
    elif market is None:
        factors["market"] = "not_found"

    score_int = int(round(max(0.0, min(100.0, score))))
    return score_int, factors

