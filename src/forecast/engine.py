from __future__ import annotations

import math
from datetime import date, datetime, timezone

from src.config import Settings
from src.data_sources.schemas import (
    ForecastAdjustment,
    ForecastAnalysis,
    MarketSnapshot,
    METARNormalized,
    ModelBundle,
    TAFNormalized,
)
from src.forecast.advection import calculate_advection_adjustment
from src.forecast.bias_correction import calculate_bias_offsets
from src.forecast.cloud_radiation import calculate_cloud_radiation_adjustment
from src.forecast.confidence import calculate_confidence
from src.forecast.ensemble import (
    calculate_model_weights,
    ensemble_sigma,
    historical_mae_sigma,
    model_spread,
    probability_sigma,
    weighted_model_tmax,
)
from src.forecast.live_adjustment import calculate_live_observation_adjustment
from src.forecast.soil_rain import calculate_rain_soil_adjustment


class LTACForecastEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(
        self,
        *,
        target_date: date,
        metar: METARNormalized | None,
        taf: TAFNormalized | None,
        model_bundle: ModelBundle,
        market: MarketSnapshot | None,
        historical_weights: dict[str, dict[str, float | None]],
    ) -> ForecastAnalysis:
        forecasts = model_bundle.forecasts
        available = model_bundle.available_forecasts
        weights = calculate_model_weights(forecasts, historical_weights)
        bias_offsets = calculate_bias_offsets(historical_weights)
        for forecast in forecasts:
            bias_offsets.setdefault(forecast.model, 0.0)
        base_tmax = weighted_model_tmax(forecasts, weights, bias_offsets)
        spread = model_spread(forecasts)
        ens_sigma = ensemble_sigma(model_bundle.ensembles)
        hist_sigma = historical_mae_sigma(historical_weights)
        uncertainty_spread = max(
            [value for value in (spread, ens_sigma) if value is not None],
            default=None,
        )
        prob_sigma = probability_sigma(
            deterministic_spread_c=spread,
            ensemble_sigma_c=ens_sigma,
            historical_sigma_c=hist_sigma,
        )
        adjustments = self._adjustments(target_date, metar, taf, forecasts)
        final = None
        if base_tmax is not None:
            final = base_tmax + sum(item.value_c for item in adjustments)
            final = round(final, 1)

        live_delta = _extract_live_delta(adjustments)
        cloud_uncertainty = _extract_adjustment_input(adjustments, "cloud_radiation", "cloud_uncertainty_pct")
        precip_spread = _extract_adjustment_input(adjustments, "rain_soil", "precip_spread_mm")
        confidence, factors = calculate_confidence(
            model_spread_c=uncertainty_spread,
            available_models=len(available),
            expected_models=len(forecasts),
            metar=metar,
            live_delta_c=live_delta,
            cloud_uncertainty_pct=cloud_uncertainty,
            precip_spread_mm=precip_spread,
            has_taf=taf is not None,
            has_history=bool(historical_weights),
            market=market,
        )
        factors["deterministic_model_spread_c"] = round(spread, 2) if spread is not None else None
        factors["ensemble_sigma_c"] = round(ens_sigma, 2) if ens_sigma is not None else None
        factors["historical_mae_sigma_c"] = round(hist_sigma, 2) if hist_sigma is not None else None
        factors["probability_sigma_c"] = round(prob_sigma, 2)
        fair_probabilities = _fair_probabilities(final, prob_sigma, market)
        edge_summary = _edge_summary(fair_probabilities, market)
        return ForecastAnalysis(
            target_date=target_date,
            generated_at=datetime.now(timezone.utc),
            report_timezone=self.settings.report_timezone,
            weighted_model_tmax_c=round(base_tmax, 2) if base_tmax is not None else None,
            final_tmax_c=final,
            main_range_low_c=round(final - 0.5, 1) if final is not None else None,
            main_range_high_c=round(final + 0.5, 1) if final is not None else None,
            model_spread_c=round(spread, 2) if spread is not None else None,
            ensemble_sigma_c=round(ens_sigma, 2) if ens_sigma is not None else None,
            probability_sigma_c=round(prob_sigma, 2),
            confidence_score=confidence,
            confidence_factors=factors,
            verdict=_verdict(final, confidence, prob_sigma),
            adjustments=adjustments,
            model_weights={key: round(value, 3) for key, value in weights.items()},
            model_bias_offsets={key: round(value, 2) for key, value in bias_offsets.items()},
            fair_probabilities=fair_probabilities,
            edge_summary=edge_summary,
            rationale_bullets=_rationale(metar, taf, forecasts, adjustments, spread, ens_sigma, prob_sigma),
            risks=_risks(adjustments, prob_sigma, taf),
        )

    def _adjustments(
        self,
        target_date: date,
        metar: METARNormalized | None,
        taf: TAFNormalized | None,
        forecasts: list,
    ) -> list[ForecastAdjustment]:
        return [
            calculate_live_observation_adjustment(metar, forecasts, target_date, self.settings.report_timezone),
            calculate_advection_adjustment(metar, forecasts),
            calculate_cloud_radiation_adjustment(forecasts),
            calculate_rain_soil_adjustment(taf, forecasts),
            ForecastAdjustment(
                name="ltac_microclimate",
                value_c=0.0,
                summary="LTAC plato/kırsal maruziyet etkisi geçmiş performansla kalibre edilecek; backtest olmadan sabit offset yok",
                inputs={"elevation_m": self.settings.ltac_elevation_m},
            ),
            ForecastAdjustment(
                name="uncertainty",
                value_c=0.0,
                summary="model/ensemble belirsizliği güven skoruna işlendi; gizli sıcaklık kaydırması yok",
                inputs={},
            ),
        ]


def _extract_adjustment_input(adjustments: list[ForecastAdjustment], name: str, key: str) -> float | None:
    for adjustment in adjustments:
        if adjustment.name == name:
            value = adjustment.inputs.get(key)
            return float(value) if value is not None else None
    return None


def _extract_live_delta(adjustments: list[ForecastAdjustment]) -> float | None:
    return _extract_adjustment_input(adjustments, "live_observation", "delta_c")


def _verdict(final: float | None, confidence: int, spread: float | None) -> str:
    if final is None:
        return "Veri eksik; tahmin üretilemedi"
    if confidence < 45:
        return f"{final:.1f}°C merkezli, düşük güvenli tahmin"
    if spread is not None and spread > 1.8:
        return f"{final:.1f}°C merkezli, belirsizlik yüksek"
    return f"{final:.1f}°C merkezli kontrollü tahmin"


def _fair_probabilities(final: float | None, sigma: float | None, market: MarketSnapshot | None) -> dict[str, float]:
    if final is None or market is None:
        return {}
    sigma = max(0.65, sigma or 0.9)
    probabilities: dict[str, float] = {}
    for outcome in market.outcomes:
        lower, upper = _bracket_bounds(outcome.bracket)
        if lower is None and upper is None:
            continue
        if lower is None:
            prob = _normal_cdf((upper - final) / sigma)
        elif upper is None:
            prob = 1.0 - _normal_cdf((lower - final) / sigma)
        else:
            prob = _normal_cdf((upper - final) / sigma) - _normal_cdf((lower - final) / sigma)
        probabilities[outcome.bracket] = round(max(0.0, min(1.0, prob)), 3)
    return probabilities


def _bracket_bounds(bracket: str) -> tuple[float | None, float | None]:
    lower = bracket.lower()
    match = __import__("re").search(r"(-?\d+(?:\.\d+)?)", lower)
    if not match:
        return None, None
    value = float(match.group(1))
    if "below" in lower or "or less" in lower:
        return None, value + 0.5
    if "higher" in lower or "or more" in lower:
        return value - 0.5, None
    return value - 0.5, value + 0.5


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _edge_summary(fair: dict[str, float], market: MarketSnapshot | None) -> str:
    if market is None or not fair:
        return "Edge yok"
    best = None
    for outcome in market.outcomes:
        probability = fair.get(outcome.bracket)
        implied = outcome.implied_probability
        if probability is None or implied is None:
            continue
        diff = probability - implied
        if best is None or diff > best[0]:
            best = (diff, outcome.bracket, probability, implied)
    if best is None or best[0] < 0.05:
        return "Edge yok"
    return f"Edge var: {best[1]} bot fair %{best[2] * 100:.0f}, piyasa %{best[3] * 100:.0f}"


def _rationale(
    metar: METARNormalized | None,
    taf: TAFNormalized | None,
    forecasts: list,
    adjustments: list[ForecastAdjustment],
    spread: float | None,
    ens_sigma: float | None,
    prob_sigma: float,
) -> list[str]:
    bullets: list[str] = []
    available = [forecast for forecast in forecasts if forecast.available and forecast.tmax_c is not None]
    if available:
        values = ", ".join(f"{forecast.model}: {forecast.tmax_c:.1f}°C" for forecast in available)
        bullets.append(f"Model tavanları: {values}.")
    unavailable = [forecast.model for forecast in forecasts if not forecast.available]
    if unavailable:
        bullets.append(f"Şu model(ler) hedef gün için veri vermedi: {', '.join(unavailable)}.")
    if spread is not None:
        bullets.append(f"Model spread {spread:.1f}°C; ayrışma güven skoruna doğrudan yansıtıldı.")
    if ens_sigma is not None and len(bullets) < 6:
        bullets.append(f"Ensemble dağılımından türetilen sigma {ens_sigma:.1f}°C; olasılık hesabında final sigma {prob_sigma:.1f}°C.")
    if metar is not None:
        bullets.append(f"Son METAR {metar.temperature_c:.0f}/{metar.dew_point_c:.0f}°C ve rüzgâr {metar.wind_direction_deg or 'VRB'}°/{metar.wind_speed_kt:.0f} kt.")
    for name in ("live_observation", "cloud_radiation", "rain_soil", "advection"):
        adj = next((item for item in adjustments if item.name == name), None)
        if adj and adj.summary and len(bullets) < 6:
            bullets.append(f"{adj.summary}; etki {adj.value_c:+.1f}°C.")
    if taf and taf.rain_or_storm_risk and len(bullets) < 6:
        bullets.append("TAF içinde SHRA/TSRA/CB riski var; öğlen ısınma tavanı belirsiz.")
    return bullets[:6]


def _risks(
    adjustments: list[ForecastAdjustment],
    spread: float | None,
    taf: TAFNormalized | None,
) -> dict[str, str]:
    cloud = next((item for item in adjustments if item.name == "cloud_radiation"), None)
    rain = next((item for item in adjustments if item.name == "rain_soil"), None)
    advection = next((item for item in adjustments if item.name == "advection"), None)
    upward = "Bulut kırılması ve daha güçlü karışım finali yukarı iter"
    downward = "Kalıcı düşük/orta bulut ve yağış evaporatif soğutma yaratır"
    if advection and advection.value_c > 0:
        upward = f"Sıcak adveksiyon devam ederse yukarı risk artar ({advection.summary})"
    if cloud and cloud.value_c < -0.7:
        downward = f"Radyasyon baskısı yüksek: {cloud.summary}"
    if rain and rain.value_c < -0.5:
        downward = f"Yağış/zemin soğutması belirgin: {rain.summary}"
    critical = "Wunderground final tam °C rounding/source davranışı"
    if spread is not None and spread > 1.5:
        critical = "Model/ensemble belirsizliği ve Wunderground rounding sınırı"
    if taf and taf.rain_or_storm_risk:
        critical = "Konvektif yağış zamanlaması + Wunderground rounding"
    return {
        "upward": upward,
        "downward": downward,
        "critical": critical,
    }
