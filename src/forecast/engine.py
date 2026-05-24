from __future__ import annotations

import math
from datetime import date, datetime, timezone
from statistics import mean

from src.config import Settings
from src.data_sources.schemas import (
    ForecastAdjustment,
    ForecastAnalysis,
    MarketSnapshot,
    METARNormalized,
    ModelBundle,
    TAFNormalized,
)
from src.forecast.ai_effect_analysis import calculate_ai_effect_analysis
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
from src.forecast.synoptic_pressure import calculate_synoptic_pressure_adjustment


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
        main_range_half_width = max(0.5, prob_sigma)
        return ForecastAnalysis(
            target_date=target_date,
            generated_at=datetime.now(timezone.utc),
            report_timezone=self.settings.report_timezone,
            weighted_model_tmax_c=round(base_tmax, 2) if base_tmax is not None else None,
            final_tmax_c=final,
            main_range_low_c=round(final - main_range_half_width, 1) if final is not None else None,
            main_range_high_c=round(final + main_range_half_width, 1) if final is not None else None,
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
            calculate_synoptic_pressure_adjustment(forecasts),
            calculate_cloud_radiation_adjustment(forecasts),
            calculate_rain_soil_adjustment(taf, forecasts),
            calculate_ai_effect_analysis(metar, forecasts),
            self._ltac_microclimate_adjustment(metar, forecasts),
            ForecastAdjustment(
                name="uncertainty",
                value_c=0.0,
                summary="model/ensemble belirsizliği güven skoruna işlendi; gizli sıcaklık kaydırması yok",
                inputs={},
            ),
        ]

    def _ltac_microclimate_adjustment(
        self,
        metar: METARNormalized | None,
        forecasts: list,
    ) -> ForecastAdjustment:
        metar_wind_dir = float(metar.wind_direction_deg) if metar and metar.wind_direction_deg is not None else None
        avg_wind_dir = metar_wind_dir if metar_wind_dir is not None else _surface_wind_direction(None, forecasts)
        sunshine_pct = _midday_sunshine_pct(forecasts)
        inputs = {
            "elevation_m": self.settings.ltac_elevation_m,
            "metar_wind_direction_deg": metar_wind_dir,
            "avg_surface_wind_direction_deg": avg_wind_dir,
            "midday_sunshine_pct": sunshine_pct,
            "westerly_runway_bias_c": self.settings.ltac_westerly_runway_bias_c,
        }
        if avg_wind_dir is None:
            return ForecastAdjustment(
                name="ltac_microclimate",
                value_c=0.0,
                summary="LTAC pist/asfalt offseti için rüzgâr yönü verisi yok",
                inputs=inputs,
            )
        if not 240 <= avg_wind_dir <= 300:
            return ForecastAdjustment(
                name="ltac_microclimate",
                value_c=0.0,
                summary=f"LTAC pist/asfalt offseti tetiklenmedi; rüzgâr {avg_wind_dir:.0f}°",
                inputs=inputs,
            )
        value = round(float(self.settings.ltac_westerly_runway_bias_c), 2)
        sunshine_text = f", güneşlenme %{sunshine_pct:.0f}" if sunshine_pct is not None else ""
        return ForecastAdjustment(
            name="ltac_microclimate",
            value_c=value,
            summary=f"Esenboğa batı rüzgârı pist/asfalt ısısını METAR termometresine taşıyor{sunshine_text}",
            inputs=inputs,
        )


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
    return f"En iyi edge: {best[1]} {best[0] * 100:+.1f}pp; bot fair %{best[2] * 100:.1f}, piyasa %{best[3] * 100:.1f}"


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
        values = ", ".join(f"{_display_model_name(forecast.model)}: {forecast.tmax_c:.1f}°C" for forecast in available)
        bullets.append(f"Model tavanları: {values}.")
    unavailable = [forecast.model for forecast in forecasts if not forecast.available]
    if unavailable:
        labels = ", ".join(_display_model_name(model) for model in unavailable)
        bullets.append(f"Şu model(ler) hedef gün için veri vermedi: {labels}.")
    if spread is not None:
        bullets.append(f"Model ayrışması {spread:.1f}°C; bu değer güven skoruna işlendi.")
    if ens_sigma is not None and len(bullets) < 6:
        bullets.append(f"Ensemble belirsizliği ±{ens_sigma:.1f}°C; olasılık hesabında ±{prob_sigma:.1f}°C kullanıldı.")
    if metar is not None:
        bullets.append(f"Son METAR {metar.temperature_c:.0f}/{metar.dew_point_c:.0f}°C ve rüzgâr {metar.wind_direction_deg or 'VRB'}°/{metar.wind_speed_kt:.0f} kt.")
    for name in ("live_observation", "synoptic_pressure", "cloud_radiation", "rain_soil", "advection"):
        adj = next((item for item in adjustments if item.name == name), None)
        if adj and adj.summary and len(bullets) < 6:
            bullets.append(f"{adj.summary}; etki {adj.value_c:+.1f}°C.")
    microclimate = next((item for item in adjustments if item.name == "ltac_microclimate"), None)
    if microclimate and microclimate.value_c != 0 and len(bullets) < 6:
        bullets.append(f"{microclimate.summary}; etki {microclimate.value_c:+.1f}°C.")
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
    live = next((item for item in adjustments if item.name == "live_observation"), None)
    synoptic = next((item for item in adjustments if item.name == "synoptic_pressure"), None)
    microclimate = next((item for item in adjustments if item.name == "ltac_microclimate"), None)
    upward_parts: list[str] = []
    downward_parts: list[str] = []
    critical_parts: list[str] = []
    if advection and advection.value_c > 0.2:
        upward_parts.append(f"sıcak adveksiyon ({advection.summary})")
    if cloud and cloud.value_c > 0.2:
        upward_parts.append(f"yüksek radyasyon/düşük bulut ({cloud.summary})")
    if live and live.value_c > 0.7:
        upward_parts.append(f"canlı gözlem model patikasından sıcak ({live.summary})")
    if synoptic and synoptic.value_c > 0.2:
        upward_parts.append(f"yükselen basınç/sıcak üst seviye ({synoptic.summary})")
    if microclimate and microclimate.value_c > 0.0:
        upward_parts.append(f"LTAC istasyon değişkeni ({microclimate.summary})")
    if cloud and cloud.value_c < -0.7:
        downward_parts.append(f"radyasyon baskısı yüksek ({cloud.summary})")
    if rain and rain.value_c < -0.5:
        downward_parts.append(f"yağış/zemin soğutması belirgin ({rain.summary})")
    if live and live.value_c < -0.7:
        downward_parts.append(f"canlı gözlem model patikasından serin ({live.summary})")
    if synoptic and synoptic.value_c < -0.3:
        downward_parts.append(f"düşen basınç/serin üst seviye ({synoptic.summary})")
    if spread is not None and spread > 1.5:
        critical_parts.append("model/ensemble ayrışması yüksek")
    if spread is None:
        critical_parts.append("model ayrışması hesaplanamadı")
    if taf and taf.rain_or_storm_risk:
        critical_parts.append("konvektif yağış zamanlaması")
    if synoptic and synoptic.inputs.get("pressure_trend_hpa") is not None:
        pressure_trend = float(synoptic.inputs["pressure_trend_hpa"])
        cape_max = synoptic.inputs.get("midday_cape_max_jkg")
        if pressure_trend <= -3.0 and cape_max is not None and float(cape_max) >= 700.0:
            critical_parts.append("düşen basınç + CAPE konveksiyon riski")
    upward = "; ".join(upward_parts) if upward_parts else "Belirgin yukarı risk sinyali yok"
    downward = "; ".join(downward_parts) if downward_parts else "Belirgin aşağı risk sinyali yok"
    critical = "; ".join(critical_parts) if critical_parts else "Belirgin kritik belirsizlik sinyali yok"
    return {
        "upward": upward,
        "downward": downward,
        "critical": critical,
    }


def _display_model_name(model: str) -> str:
    lowered = model.lower()
    if "icon_eu" in lowered:
        return "ICON-EU"
    if "icon_global" in lowered:
        return "ICON-Global"
    if "ecmwf" in lowered:
        return "ECMWF"
    if "gfs" in lowered:
        return "GFS"
    if "icon" in lowered:
        return "ICON"
    return model


def _surface_wind_direction(metar: METARNormalized | None, forecasts: list) -> float | None:
    directions: list[float] = []
    if metar and metar.wind_direction_deg is not None:
        directions.append(float(metar.wind_direction_deg))
    for forecast in forecasts:
        for point in forecast.midday_points:
            if point.wind_direction_10m_deg is not None:
                directions.append(float(point.wind_direction_10m_deg))
    return _circular_mean(directions) if directions else None


def _midday_sunshine_pct(forecasts: list) -> float | None:
    shortwave = []
    opacity_values = []
    for forecast in forecasts:
        for point in forecast.midday_points:
            if point.shortwave_radiation_wm2 is not None:
                shortwave.append(float(point.shortwave_radiation_wm2))
            layer_values = [
                (point.cloud_cover_low_pct, 0.70),
                (point.cloud_cover_mid_pct, 0.45),
                (point.cloud_cover_high_pct, 0.25),
            ]
            layer_opacity = sum(float(value) * weight for value, weight in layer_values if value is not None)
            if layer_opacity:
                opacity_values.append(min(100.0, layer_opacity))
            elif point.cloud_cover_pct is not None:
                opacity_values.append(float(point.cloud_cover_pct) * 0.65)
    candidates = []
    if shortwave:
        candidates.append(max(shortwave) / 850.0 * 100.0)
    if opacity_values:
        candidates.append(100.0 - mean(opacity_values))
    if not candidates:
        return None
    return round(max(0.0, min(100.0, mean(candidates))), 1)


def _circular_mean(values: list[float]) -> float:
    sin_sum = sum(math.sin(math.radians(value)) for value in values)
    cos_sum = sum(math.cos(math.radians(value)) for value in values)
    angle = math.degrees(math.atan2(sin_sum, cos_sum))
    return angle + 360 if angle < 0 else angle
