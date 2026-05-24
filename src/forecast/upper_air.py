from __future__ import annotations

from statistics import mean

from src.data_sources.schemas import ForecastAdjustment, ModelForecast, ModelHourlyPoint


def calculate_upper_air_profile_adjustment(forecasts: list[ModelForecast]) -> ForecastAdjustment:
    morning = _points(forecasts, 6, 9)
    midday = _points(forecasts, 10, 14)
    if not morning and not midday:
        return ForecastAdjustment(name="upper_air_profile", value_c=0.0, summary="üst seviye/profil verisi yok", inputs={})

    temp_925_mid = _avg(_values(midday, "temperature_925hpa_c"))
    temp_850_mid = _avg(_values(midday, "temperature_850hpa_c"))
    height_500_mid = _avg(_values(midday, "geopotential_height_500hpa_m"))
    jet_250_max = _max(_values(midday, "wind_speed_250hpa_kt") + _values(morning, "wind_speed_250hpa_kt"))
    rh_700_mid = _avg(_values(midday, "relative_humidity_700hpa_pct"))
    cape_max = _max(_values(midday, "cape_jkg"))

    surface_morning = _avg(_values(morning, "temperature_2m_c"))
    layer_morning = _avg(_values(morning, "temperature_925hpa_c"))
    if layer_morning is None:
        layer_morning = _avg(_values(morning, "temperature_850hpa_c"))
    inversion_strength = layer_morning - surface_morning if layer_morning is not None and surface_morning is not None else None

    value = 0.0
    if inversion_strength is not None:
        if inversion_strength >= 4.0:
            value -= 0.35
        elif inversion_strength >= 2.0:
            value -= 0.2
    if temp_850_mid is not None:
        if temp_850_mid >= 16.0:
            value += 0.25
        elif temp_850_mid <= 5.0:
            value -= 0.25
    if height_500_mid is not None:
        if height_500_mid >= 5800:
            value += 0.15
        elif height_500_mid <= 5600:
            value -= 0.2
    if rh_700_mid is not None:
        if rh_700_mid >= 80:
            value -= 0.2
        elif rh_700_mid <= 35:
            value += 0.1
    if cape_max is not None and cape_max >= 1000:
        value -= 0.15

    summary = _summary(
        inversion_strength=inversion_strength,
        temp_925_mid=temp_925_mid,
        temp_850_mid=temp_850_mid,
        height_500_mid=height_500_mid,
        jet_250_max=jet_250_max,
        rh_700_mid=rh_700_mid,
        cape_max=cape_max,
    )
    return ForecastAdjustment(
        name="upper_air_profile",
        value_c=round(max(-0.8, min(0.5, value)), 2),
        summary=summary,
        inputs={
            "morning_inversion_strength_c": inversion_strength,
            "midday_925hpa_temp_c": temp_925_mid,
            "midday_850hpa_temp_c": temp_850_mid,
            "midday_500hpa_height_m": height_500_mid,
            "max_250hpa_wind_kt": jet_250_max,
            "midday_700hpa_relative_humidity_pct": rh_700_mid,
            "max_cape_jkg": cape_max,
        },
    )


def _points(forecasts: list[ModelForecast], start_hour: int, end_hour: int) -> list[ModelHourlyPoint]:
    points: list[ModelHourlyPoint] = []
    for forecast in forecasts:
        if not forecast.available:
            continue
        points.extend(point for point in forecast.hourly if start_hour <= point.time.hour <= end_hour)
    return points


def _values(points: list[ModelHourlyPoint], field: str) -> list[float]:
    values = []
    for point in points:
        value = getattr(point, field)
        if value is not None:
            values.append(float(value))
    return values


def _avg(values: list[float]) -> float | None:
    return mean(values) if values else None


def _max(values: list[float]) -> float | None:
    return max(values) if values else None


def _summary(
    *,
    inversion_strength: float | None,
    temp_925_mid: float | None,
    temp_850_mid: float | None,
    height_500_mid: float | None,
    jet_250_max: float | None,
    rh_700_mid: float | None,
    cape_max: float | None,
) -> str:
    parts: list[str] = []
    if inversion_strength is not None:
        if inversion_strength >= 2.0:
            parts.append(f"sabah inversiyon +{inversion_strength:.1f}°C")
        else:
            parts.append(f"sabah inversiyon zayıf ({inversion_strength:+.1f}°C)")
    if temp_925_mid is not None:
        parts.append(f"925 hPa {temp_925_mid:.1f}°C")
    if temp_850_mid is not None:
        parts.append(f"850 hPa {temp_850_mid:.1f}°C")
    if height_500_mid is not None:
        parts.append(f"500 hPa {height_500_mid:.0f} m")
    if jet_250_max is not None:
        parts.append(f"250 hPa jet {jet_250_max:.0f} kt")
    if rh_700_mid is not None:
        parts.append(f"700 hPa nem %{rh_700_mid:.0f}")
    if cape_max is not None:
        parts.append(f"CAPE {cape_max:.0f} J/kg")
    return ", ".join(parts) if parts else "üst seviye/profil verisi yok"
