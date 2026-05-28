from __future__ import annotations

from statistics import mean, pstdev

from src.data_sources.schemas import ForecastAdjustment, ModelForecast, TAFNormalized


def calculate_rain_soil_adjustment(
    taf: TAFNormalized | None,
    forecasts: list[ModelForecast],
) -> ForecastAdjustment:
    totals = []
    midday_precip = []
    soil_moisture = []
    soil_temperature = []
    for forecast in forecasts:
        values = [point.precipitation_mm for point in forecast.hourly if point.precipitation_mm is not None]
        if values:
            totals.append(sum(values))
        midday = [
            point.precipitation_mm
            for point in forecast.midday_points
            if point.precipitation_mm is not None
        ]
        if midday:
            midday_precip.append(sum(midday))
        for point in forecast.midday_points:
            if point.soil_moisture_0_to_1cm_m3m3 is not None:
                soil_moisture.append(point.soil_moisture_0_to_1cm_m3m3)
            if point.soil_temperature_0cm_c is not None:
                soil_temperature.append(point.soil_temperature_0cm_c)
    taf_risk = bool(taf and taf.rain_or_storm_risk)
    if not totals and not taf_risk and not soil_moisture:
        return ForecastAdjustment(name="rain_soil", value_c=0.0, summary="yağış verisi yok veya düşük", inputs={})

    avg_total = mean(totals) if totals else 0.0
    avg_midday = mean(midday_precip) if midday_precip else 0.0
    avg_soil_moisture = mean(soil_moisture) if soil_moisture else None
    avg_soil_temp = mean(soil_temperature) if soil_temperature else None
    spread = pstdev(totals) if len(totals) >= 2 else 0.0
    value = 0.0
    if avg_total >= 5.0:
        value -= 0.55
    elif avg_total >= 1.5:
        value -= 0.25
    if avg_midday >= 1.0:
        value -= 0.45
    if taf_risk:
        value -= 0.25
    if avg_soil_moisture is not None:
        if avg_soil_moisture >= 0.28:
            value -= 0.2
        elif avg_soil_moisture <= 0.12:
            value += 0.1
    if avg_soil_temp is not None and avg_soil_temp >= 28.0 and avg_total < 1.5:
        value += 0.1
    summary_parts = [
        f"model yağış ort. {avg_total:.1f} mm",
        f"öğlen {avg_midday:.1f} mm",
        f"TAF risk {'var' if taf_risk else 'yok'}",
    ]
    if avg_soil_moisture is not None:
        summary_parts.append(f"üst toprak nemi {avg_soil_moisture:.2f} m³/m³")
    if avg_soil_temp is not None:
        summary_parts.append(f"toprak yüzeyi {avg_soil_temp:.1f}°C")
    return ForecastAdjustment(
        name="rain_soil",
        value_c=round(max(-1.4, min(0.2, value)), 2),
        summary=", ".join(summary_parts),
        inputs={
            "avg_total_precip_mm": avg_total,
            "avg_midday_precip_mm": avg_midday,
            "precip_spread_mm": spread,
            "taf_risk": taf_risk,
            "avg_soil_moisture_m3m3": avg_soil_moisture,
            "avg_soil_temperature_c": avg_soil_temp,
        },
    )
