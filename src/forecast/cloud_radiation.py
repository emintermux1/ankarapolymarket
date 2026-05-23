from __future__ import annotations

from statistics import mean, pstdev

from src.data_sources.schemas import ForecastAdjustment, ModelForecast


def calculate_cloud_radiation_adjustment(forecasts: list[ModelForecast]) -> ForecastAdjustment:
    low = []
    mid = []
    high = []
    shortwave = []
    for forecast in forecasts:
        for point in forecast.midday_points:
            if point.cloud_cover_low_pct is not None:
                low.append(point.cloud_cover_low_pct)
            if point.cloud_cover_mid_pct is not None:
                mid.append(point.cloud_cover_mid_pct)
            if point.cloud_cover_high_pct is not None:
                high.append(point.cloud_cover_high_pct)
            if point.shortwave_radiation_wm2 is not None:
                shortwave.append(point.shortwave_radiation_wm2)
    if not low and not mid and not shortwave:
        return ForecastAdjustment(name="cloud_radiation", value_c=0.0, summary="cloud/radiation unavailable", inputs={})

    low_mean = mean(low) if low else None
    mid_mean = mean(mid) if mid else None
    high_mean = mean(high) if high else None
    shortwave_max = max(shortwave) if shortwave else None
    value = 0.0
    if low_mean is not None:
        value -= 0.8 * min(1.0, low_mean / 100.0)
    if mid_mean is not None:
        value -= 0.45 * min(1.0, mid_mean / 100.0)
    if shortwave_max is not None and shortwave_max < 550:
        value -= 0.35
    if shortwave_max is not None and shortwave_max > 750 and (low_mean or 0) < 35:
        value += 0.35
    cloud_uncertainty = pstdev(low + mid) if len(low + mid) >= 2 else None
    summary_parts = []
    if low_mean is not None:
        summary_parts.append(f"10-14 alçak bulut %{low_mean:.0f}")
    if mid_mean is not None:
        summary_parts.append(f"orta bulut %{mid_mean:.0f}")
    if high_mean is not None:
        summary_parts.append(f"yüksek bulut %{high_mean:.0f}")
    if shortwave_max is not None:
        summary_parts.append(f"maks kısa dalga {shortwave_max:.0f} W/m²")
    return ForecastAdjustment(
        name="cloud_radiation",
        value_c=round(max(-1.6, min(0.6, value)), 2),
        summary=", ".join(summary_parts),
        inputs={
            "low_cloud_mean_pct": low_mean,
            "mid_cloud_mean_pct": mid_mean,
            "high_cloud_mean_pct": high_mean,
            "shortwave_max_wm2": shortwave_max,
            "cloud_uncertainty_pct": cloud_uncertainty,
        },
    )

