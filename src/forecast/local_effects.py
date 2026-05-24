from __future__ import annotations

from datetime import date
from statistics import mean
from zoneinfo import ZoneInfo

from src.data_sources.schemas import ForecastAdjustment, METARNormalized, ModelForecast, RadarMotionSignal


def calculate_radar_motion_adjustment(
    radar: RadarMotionSignal | None,
    target_date: date,
    report_timezone: str,
) -> ForecastAdjustment:
    if radar is None:
        return ForecastAdjustment(name="radar_motion", value_c=0.0, summary="canlı radar verisi yok", inputs={})
    if radar.frame_time is not None and radar.frame_time.astimezone(ZoneInfo(report_timezone)).date() != target_date:
        return ForecastAdjustment(
            name="radar_motion",
            value_c=0.0,
            summary="canlı radar hedef gün değil",
            inputs={"frame_time": radar.frame_time.isoformat(), "target_date": target_date.isoformat()},
        )

    center = radar.center_intensity or 0.0
    upwind = radar.upwind_intensity or 0.0
    nearby = radar.max_nearby_intensity or 0.0
    value = 0.0
    if radar.motion in {"overhead", "intensifying_overhead"}:
        value -= 0.65 if center >= 12 else 0.4
    elif radar.motion == "approaching":
        value -= 0.45 if upwind >= 10 else 0.25
    elif radar.motion == "nearby" and nearby >= 8:
        value -= 0.15

    summary = {
        "no_echo": "canlı radar ekosu yok",
        "approaching": "canlı radar hücresi LTAC yönüne yaklaşıyor",
        "departing": "canlı radar hücresi LTAC'tan uzaklaşıyor",
        "overhead": "canlı radar ekosu LTAC üzerinde",
        "intensifying_overhead": "canlı radar ekosu LTAC üzerinde güçleniyor",
        "nearby": "canlı radar ekosu çevrede",
    }.get(radar.motion, f"canlı radar durumu {radar.motion}")
    return ForecastAdjustment(
        name="radar_motion",
        value_c=round(max(-0.9, min(0.0, value)), 2),
        summary=f"{summary}; merkez indeks {center:.1f}, upwind {upwind:.1f}",
        inputs={
            "motion": radar.motion,
            "confidence": radar.confidence,
            "center_intensity": radar.center_intensity,
            "previous_center_intensity": radar.previous_center_intensity,
            "upwind_intensity": radar.upwind_intensity,
            "downwind_intensity": radar.downwind_intensity,
            "max_nearby_intensity": radar.max_nearby_intensity,
            "frame_time": radar.frame_time.isoformat() if radar.frame_time else None,
        },
    )


def calculate_satellite_cloud_cooling_adjustment(forecasts: list[ModelForecast]) -> ForecastAdjustment:
    early_cloud = []
    midday_cloud = []
    midday_low_mid = []
    shortwave = []
    shortwave_deficit = []
    for forecast in forecasts:
        if not forecast.available:
            continue
        for point in forecast.hourly:
            cloud = _cloud_proxy(point.cloud_cover_pct, point.cloud_cover_low_pct, point.cloud_cover_mid_pct)
            if cloud is not None:
                if 8 <= point.time.hour <= 10:
                    early_cloud.append(cloud)
                if 11 <= point.time.hour <= 14:
                    midday_cloud.append(cloud)
                if 10 <= point.time.hour <= 14:
                    low_mid = _cloud_proxy(None, point.cloud_cover_low_pct, point.cloud_cover_mid_pct)
                    if low_mid is not None:
                        midday_low_mid.append(low_mid)
            if 10 <= point.time.hour <= 14 and point.shortwave_radiation_wm2 is not None:
                shortwave.append(point.shortwave_radiation_wm2)
                shortwave_deficit.append(max(0.0, 760.0 - point.shortwave_radiation_wm2))

    if not midday_cloud and not shortwave:
        return ForecastAdjustment(name="satellite_cloud_cooling", value_c=0.0, summary="uydu/proxy bulut soğuma verisi yok", inputs={})

    early_mean = mean(early_cloud) if early_cloud else None
    midday_mean = mean(midday_cloud) if midday_cloud else None
    low_mid_mean = mean(midday_low_mid) if midday_low_mid else None
    shortwave_mean = mean(shortwave) if shortwave else None
    deficit_mean = mean(shortwave_deficit) if shortwave_deficit else 0.0
    cloud_growth = (midday_mean - early_mean) if midday_mean is not None and early_mean is not None else None
    value = 0.0
    if low_mid_mean is not None and low_mid_mean >= 70:
        value -= 0.25
    if cloud_growth is not None and cloud_growth >= 25:
        value -= 0.2
    if deficit_mean >= 260:
        value -= 0.25
    elif deficit_mean >= 150:
        value -= 0.1

    summary_parts = []
    if midday_mean is not None:
        summary_parts.append(f"öğlen bulut proxy %{midday_mean:.0f}")
    if cloud_growth is not None:
        summary_parts.append(f"bulut artışı {cloud_growth:+.0f}pp")
    if shortwave_mean is not None:
        summary_parts.append(f"ortalama kısa dalga {shortwave_mean:.0f} W/m²")
    return ForecastAdjustment(
        name="satellite_cloud_cooling",
        value_c=round(max(-0.7, min(0.0, value)), 2),
        summary=", ".join(summary_parts),
        inputs={
            "early_cloud_proxy_pct": early_mean,
            "midday_cloud_proxy_pct": midday_mean,
            "low_mid_cloud_proxy_pct": low_mid_mean,
            "cloud_growth_pp": cloud_growth,
            "shortwave_mean_wm2": shortwave_mean,
            "shortwave_deficit_wm2": deficit_mean,
        },
    )


def calculate_metar_anomaly_adjustment(
    metar: METARNormalized | None,
    forecasts: list[ModelForecast],
    target_date: date,
    report_timezone: str,
) -> ForecastAdjustment:
    if metar is None:
        return ForecastAdjustment(name="metar_anomaly", value_c=0.0, summary="METAR anomalisi kontrol edilemedi", inputs={})
    local_time = metar.observation_time.astimezone(ZoneInfo(report_timezone))
    if local_time.date() != target_date:
        return ForecastAdjustment(
            name="metar_anomaly",
            value_c=0.0,
            summary="METAR anomalisi hedef gün dışında",
            inputs={"observation_date": local_time.date().isoformat(), "target_date": target_date.isoformat()},
        )

    expected_values = [
        point.temperature_2m_c
        for forecast in forecasts
        if forecast.available
        for point in forecast.hourly
        if point.time.hour == local_time.hour and point.temperature_2m_c is not None
    ]
    expected = mean(expected_values) if expected_values else None
    delta = metar.temperature_c - expected if expected is not None else None
    flags: list[str] = []
    severity = 0.0
    if delta is not None:
        abs_delta = abs(delta)
        if abs_delta >= 5.0:
            flags.append(f"saatlik modelden {delta:+.1f}°C ayrışma")
            severity = max(severity, 0.9)
        elif abs_delta >= 3.0:
            flags.append(f"saatlik modelden {delta:+.1f}°C ayrışma")
            severity = max(severity, 0.55)
    if metar.relative_humidity is not None and metar.relative_humidity <= 12:
        flags.append(f"çok kuru METAR RH %{metar.relative_humidity}")
        severity = max(severity, 0.35)
    if metar.pressure_hpa is not None and not 940 <= metar.pressure_hpa <= 1045:
        flags.append(f"basınç olağandışı {metar.pressure_hpa:.0f} hPa")
        severity = max(severity, 0.45)
    if metar.wind_gust_kt is not None and metar.wind_gust_kt - metar.wind_speed_kt >= 25:
        flags.append(f"ani hamle farkı {metar.wind_gust_kt - metar.wind_speed_kt:.0f} kt")
        severity = max(severity, 0.35)
    if metar.is_stale:
        flags.append(f"METAR eski {metar.age_minutes:.0f} dk")
        severity = max(severity, 0.5)

    summary = "METAR anomalisi yok" if not flags else "METAR anomalisi: " + "; ".join(flags)
    return ForecastAdjustment(
        name="metar_anomaly",
        value_c=0.0,
        summary=summary,
        inputs={
            "severity": round(severity, 2),
            "metar_temp_c": metar.temperature_c,
            "model_expected_c": expected,
            "delta_c": delta,
            "flags": flags,
        },
    )


def calculate_microclimate_adjustment(
    metar: METARNormalized | None,
    forecasts: list[ModelForecast],
    elevation_m: int,
) -> ForecastAdjustment:
    midday_rh = [
        point.relative_humidity_pct
        for forecast in forecasts
        if forecast.available
        for point in forecast.midday_points
        if point.relative_humidity_pct is not None
    ]
    midday_wind = [
        point.wind_speed_10m_kt
        for forecast in forecasts
        if forecast.available
        for point in forecast.midday_points
        if point.wind_speed_10m_kt is not None
    ]
    rh = metar.relative_humidity if metar and metar.relative_humidity is not None else (mean(midday_rh) if midday_rh else None)
    wind = metar.wind_speed_kt if metar else (mean(midday_wind) if midday_wind else None)
    value = -0.15 if elevation_m >= 900 else 0.0
    if rh is not None and rh <= 35:
        value += 0.15
    if wind is not None and wind >= 14:
        value -= 0.1
    elif wind is not None and wind <= 5:
        value += 0.05
    summary = f"Esenboğa plato mikrokliması; rakım {elevation_m} m"
    if rh is not None:
        summary += f", RH %{rh:.0f}"
    if wind is not None:
        summary += f", rüzgâr {wind:.0f} kt"
    return ForecastAdjustment(
        name="ltac_microclimate",
        value_c=round(max(-0.35, min(0.25, value)), 2),
        summary=summary,
        inputs={"elevation_m": elevation_m, "humidity_pct": rh, "wind_speed_kt": wind},
    )


def calculate_airport_heat_island_adjustment(
    metar: METARNormalized | None,
    forecasts: list[ModelForecast],
) -> ForecastAdjustment:
    low_cloud = _mean_midday(forecasts, "cloud_cover_low_pct")
    shortwave = _max_midday(forecasts, "shortwave_radiation_wm2")
    wind = metar.wind_speed_kt if metar else _mean_midday(forecasts, "wind_speed_10m_kt")
    rh = metar.relative_humidity if metar and metar.relative_humidity is not None else _mean_midday(forecasts, "relative_humidity_pct")
    value = 0.0
    if (low_cloud is None or low_cloud <= 35) and (shortwave is None or shortwave >= 650):
        value += 0.15
    if wind is not None and wind <= 7:
        value += 0.1
    if rh is not None and rh <= 30:
        value += 0.05
    if low_cloud is not None and low_cloud >= 70:
        value = 0.0
    summary = "havalimanı yüzey ısı adası"
    if low_cloud is not None:
        summary += f"; alçak bulut %{low_cloud:.0f}"
    if wind is not None:
        summary += f", rüzgâr {wind:.0f} kt"
    return ForecastAdjustment(
        name="airport_heat_island",
        value_c=round(max(0.0, min(0.3, value)), 2),
        summary=summary,
        inputs={"low_cloud_mean_pct": low_cloud, "shortwave_max_wm2": shortwave, "wind_speed_kt": wind, "humidity_pct": rh},
    )


def calculate_runway_radiation_adjustment(
    metar: METARNormalized | None,
    forecasts: list[ModelForecast],
) -> ForecastAdjustment:
    shortwave = _max_midday(forecasts, "shortwave_radiation_wm2")
    low_cloud = _mean_midday(forecasts, "cloud_cover_low_pct")
    wind = metar.wind_speed_kt if metar else _mean_midday(forecasts, "wind_speed_10m_kt")
    value = 0.0
    if shortwave is not None and shortwave >= 760 and (low_cloud is None or low_cloud <= 30):
        value += 0.25
    elif shortwave is not None and shortwave >= 680 and (low_cloud is None or low_cloud <= 45):
        value += 0.12
    if wind is not None and wind <= 6 and value > 0:
        value += 0.08
    if wind is not None and wind >= 15:
        value *= 0.4
    summary_parts = []
    if shortwave is not None:
        summary_parts.append(f"maks kısa dalga {shortwave:.0f} W/m²")
    if low_cloud is not None:
        summary_parts.append(f"alçak bulut %{low_cloud:.0f}")
    if wind is not None:
        summary_parts.append(f"rüzgâr {wind:.0f} kt")
    return ForecastAdjustment(
        name="runway_radiation",
        value_c=round(max(0.0, min(0.35, value)), 2),
        summary="pist radyasyon ısınması: " + (", ".join(summary_parts) if summary_parts else "veri yok"),
        inputs={"shortwave_max_wm2": shortwave, "low_cloud_mean_pct": low_cloud, "wind_speed_kt": wind},
    )


def _cloud_proxy(total: float | None, low: float | None, mid: float | None) -> float | None:
    values = [value for value in (total, low, mid) if value is not None]
    if not values:
        return None
    if total is not None and (low is not None or mid is not None):
        return max(total, mean(value for value in (low, mid) if value is not None))
    return mean(values)


def _mean_midday(forecasts: list[ModelForecast], field: str) -> float | None:
    values = [
        getattr(point, field)
        for forecast in forecasts
        if forecast.available
        for point in forecast.midday_points
        if getattr(point, field) is not None
    ]
    return mean(values) if values else None


def _max_midday(forecasts: list[ModelForecast], field: str) -> float | None:
    values = [
        getattr(point, field)
        for forecast in forecasts
        if forecast.available
        for point in forecast.midday_points
        if getattr(point, field) is not None
    ]
    return max(values) if values else None
