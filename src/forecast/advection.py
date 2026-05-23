from __future__ import annotations

from statistics import mean

from src.data_sources.schemas import ForecastAdjustment, METARNormalized, ModelForecast


def calculate_advection_adjustment(
    metar: METARNormalized | None,
    forecasts: list[ModelForecast],
) -> ForecastAdjustment:
    surface_dirs = []
    if metar and metar.wind_direction_deg is not None:
        surface_dirs.append(float(metar.wind_direction_deg))
    wind_850_dirs = []
    temp_850_values = []
    for forecast in forecasts:
        for point in forecast.midday_points:
            if point.wind_direction_850hpa_deg is not None:
                wind_850_dirs.append(point.wind_direction_850hpa_deg)
            if point.temperature_850hpa_c is not None:
                temp_850_values.append(point.temperature_850hpa_c)
    direction_inputs = surface_dirs + wind_850_dirs
    if not direction_inputs:
        return ForecastAdjustment(name="advection", value_c=0.0, summary="wind direction unavailable", inputs={})

    avg_dir = _circular_mean(direction_inputs)
    avg_850_temp = mean(temp_850_values) if temp_850_values else None
    value = 0.0
    if 160 <= avg_dir <= 250:
        value += 0.45
    elif avg_dir <= 80 or avg_dir >= 320:
        value -= 0.45
    if avg_850_temp is not None:
        if avg_850_temp >= 12.0:
            value += 0.2
        elif avg_850_temp <= 8.0:
            value -= 0.2
    summary = f"ortalama akış {avg_dir:.0f}°"
    if avg_850_temp is not None:
        summary += f", 850 hPa {avg_850_temp:.1f}°C"
    return ForecastAdjustment(
        name="advection",
        value_c=round(max(-0.9, min(0.9, value)), 2),
        summary=summary,
        inputs={"avg_direction_deg": avg_dir, "avg_850_temp_c": avg_850_temp},
    )


def _circular_mean(values: list[float]) -> float:
    import math

    sin_sum = sum(math.sin(math.radians(value)) for value in values)
    cos_sum = sum(math.cos(math.radians(value)) for value in values)
    angle = math.degrees(math.atan2(sin_sum, cos_sum))
    return angle + 360 if angle < 0 else angle

