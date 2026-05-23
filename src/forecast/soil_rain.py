from __future__ import annotations

from statistics import mean, pstdev

from src.data_sources.schemas import ForecastAdjustment, ModelForecast, TAFNormalized


def calculate_rain_soil_adjustment(
    taf: TAFNormalized | None,
    forecasts: list[ModelForecast],
) -> ForecastAdjustment:
    totals = []
    midday_precip = []
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
    taf_risk = bool(taf and taf.rain_or_storm_risk)
    if not totals and not taf_risk:
        return ForecastAdjustment(name="rain_soil", value_c=0.0, summary="yağış verisi yok veya düşük", inputs={})

    avg_total = mean(totals) if totals else 0.0
    avg_midday = mean(midday_precip) if midday_precip else 0.0
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
    return ForecastAdjustment(
        name="rain_soil",
        value_c=round(max(-1.4, min(0.0, value)), 2),
        summary=f"model yağış ort. {avg_total:.1f} mm, öğlen {avg_midday:.1f} mm, TAF risk {'var' if taf_risk else 'yok'}",
        inputs={"avg_total_precip_mm": avg_total, "avg_midday_precip_mm": avg_midday, "precip_spread_mm": spread, "taf_risk": taf_risk},
    )
