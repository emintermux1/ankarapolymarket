from __future__ import annotations

from datetime import date
from statistics import mean
from zoneinfo import ZoneInfo

from src.data_sources.schemas import ForecastAdjustment, METARNormalized, ModelForecast


def calculate_live_observation_adjustment(
    metar: METARNormalized | None,
    forecasts: list[ModelForecast],
    target_date: date,
    report_timezone: str,
) -> ForecastAdjustment:
    if metar is None:
        return ForecastAdjustment(name="live_observation", value_c=0.0, summary="METAR verisi yok", inputs={})
    local_observation_date = metar.observation_time.astimezone(ZoneInfo(report_timezone)).date()
    if local_observation_date != target_date:
        return ForecastAdjustment(
            name="live_observation",
            value_c=0.0,
            summary=f"METAR {local_observation_date.isoformat()} tarihli; hedef gün canlı düzeltmesi beklemede",
            inputs={"metar_temp_c": metar.temperature_c, "target_date": target_date.isoformat()},
        )
    expected_values = [
        forecast.expected_temp_at_report_hour_c
        for forecast in forecasts
        if forecast.available and forecast.expected_temp_at_report_hour_c is not None
    ]
    if not expected_values:
        return ForecastAdjustment(
            name="live_observation",
            value_c=0.0,
            summary="09:00 model patikası verisi yok",
            inputs={"metar_temp_c": metar.temperature_c},
        )
    expected = float(mean(expected_values))
    delta = metar.temperature_c - expected
    if metar.is_stale:
        return ForecastAdjustment(
            name="live_observation",
            value_c=0.0,
            summary=f"METAR eski ({metar.age_minutes:.0f} dk); canlı düzeltme kapalı",
            inputs={"metar_temp_c": metar.temperature_c, "model_expected_c": expected, "delta_c": delta},
        )
    adjustment = max(-1.5, min(1.5, delta * 0.35))
    if abs(delta) < 0.4:
        summary = "METAR model path ile uyumlu"
    elif delta > 0:
        summary = f"METAR model 09:00 patikasından {delta:.1f}°C sıcak"
    else:
        summary = f"METAR model 09:00 patikasından {abs(delta):.1f}°C geride"
    return ForecastAdjustment(
        name="live_observation",
        value_c=round(adjustment, 2),
        summary=summary,
        inputs={"metar_temp_c": metar.temperature_c, "model_expected_c": expected, "delta_c": delta},
    )
