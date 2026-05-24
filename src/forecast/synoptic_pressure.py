from __future__ import annotations

from statistics import mean

from src.data_sources.schemas import ForecastAdjustment, ModelForecast


def calculate_synoptic_pressure_adjustment(forecasts: list[ModelForecast]) -> ForecastAdjustment:
    morning_pressure = []
    midday_pressure = []
    midday_850_temp = []
    midday_500_height = []
    midday_cape = []
    for forecast in forecasts:
        for point in forecast.hourly:
            if point.pressure_msl_hpa is not None:
                if 6 <= point.time.hour <= 9:
                    morning_pressure.append(point.pressure_msl_hpa)
                if 12 <= point.time.hour <= 15:
                    midday_pressure.append(point.pressure_msl_hpa)
            if 10 <= point.time.hour <= 14:
                if point.temperature_850hpa_c is not None:
                    midday_850_temp.append(point.temperature_850hpa_c)
                if point.geopotential_height_500hpa_m is not None:
                    midday_500_height.append(point.geopotential_height_500hpa_m)
                if point.cape_jkg is not None:
                    midday_cape.append(point.cape_jkg)

    pressure_trend = _delta(morning_pressure, midday_pressure)
    pressure_mean = mean(midday_pressure) if midday_pressure else None
    temp_850_mean = mean(midday_850_temp) if midday_850_temp else None
    height_500_mean = mean(midday_500_height) if midday_500_height else None
    cape_max = max(midday_cape) if midday_cape else None
    if pressure_trend is None and temp_850_mean is None and height_500_mean is None:
        return ForecastAdjustment(
            name="synoptic_pressure",
            value_c=0.0,
            summary="basınç/üst seviye verisi yok",
            inputs={},
        )

    value = 0.0
    if pressure_trend is not None:
        if pressure_trend <= -3.0:
            value -= 0.35
        elif pressure_trend <= -1.5:
            value -= 0.2
        elif pressure_trend >= 3.0:
            value += 0.25
        elif pressure_trend >= 1.5:
            value += 0.15

    if temp_850_mean is not None and height_500_mean is not None:
        if temp_850_mean >= 15.0 and height_500_mean >= 5780.0:
            value += 0.25
        elif temp_850_mean <= 9.0 or height_500_mean <= 5700.0:
            value -= 0.25
    elif temp_850_mean is not None:
        if temp_850_mean >= 17.0:
            value += 0.15
        elif temp_850_mean <= 8.0:
            value -= 0.15

    if (
        cape_max is not None
        and cape_max >= 700.0
        and pressure_trend is not None
        and pressure_trend < -1.0
    ):
        value -= 0.2

    parts = []
    if pressure_trend is not None:
        parts.append(f"06-09→12-15 basınç trendi {pressure_trend:+.1f} hPa")
    if pressure_mean is not None:
        parts.append(f"öğlen MSLP {pressure_mean:.0f} hPa")
    if temp_850_mean is not None:
        parts.append(f"850 hPa {temp_850_mean:.1f}°C")
    if height_500_mean is not None:
        parts.append(f"500 hPa {height_500_mean:.0f} m")
    if cape_max is not None and cape_max >= 300.0:
        parts.append(f"CAPE {cape_max:.0f} J/kg")
    return ForecastAdjustment(
        name="synoptic_pressure",
        value_c=round(max(-0.8, min(0.6, value)), 2),
        summary=", ".join(parts),
        inputs={
            "pressure_trend_hpa": pressure_trend,
            "midday_pressure_msl_hpa": pressure_mean,
            "midday_850_temp_c": temp_850_mean,
            "midday_500_height_m": height_500_mean,
            "midday_cape_max_jkg": cape_max,
        },
    )


def _delta(early: list[float], late: list[float]) -> float | None:
    if not early or not late:
        return None
    return mean(late) - mean(early)
