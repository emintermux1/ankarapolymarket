from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


ECMWF_HRES_VARIABLES = [
    "temperature_2m",
    "temperature_2m_max",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "precipitation",
    "shortwave_radiation",
    "wind_speed_10m",
    "wind_direction_10m",
    "pressure_msl",
    "surface_pressure",
    "cape",
    "total_column_integrated_water_vapour",
    "surface_temperature",
    "soil_moisture_0_to_7cm",
]


class OpenMeteoECMWFAdapter(HttpSource):
    source_name = "Open-Meteo ECMWF HRES"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.url = "https://api.open-meteo.com/v1/ecmwf"

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        payload = await self._request_json(
            self.url,
            params={
                "latitude": self.settings.ltac_latitude,
                "longitude": self.settings.ltac_longitude,
                "elevation": self.settings.ltac_elevation_m,
                "hourly": ",".join(ECMWF_HRES_VARIABLES),
                "timezone": self.settings.report_timezone,
                "forecast_days": 15,
                "wind_speed_unit": "kn",
                "cell_selection": "land",
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "Forecast payload is not an object")
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        points: list[ModelHourlyPoint] = []
        tz = ZoneInfo(self.settings.report_timezone)
        for idx, ts in enumerate(times):
            if not str(ts).startswith(target_date.isoformat()):
                continue
            points.append(
                ModelHourlyPoint(
                    time=datetime.fromisoformat(str(ts)).replace(tzinfo=tz),
                    temperature_2m_c=_series_value(hourly, "temperature_2m", idx),
                    cloud_cover_pct=_series_value(hourly, "cloud_cover", idx),
                    cloud_cover_low_pct=_series_value(hourly, "cloud_cover_low", idx),
                    cloud_cover_mid_pct=_series_value(hourly, "cloud_cover_mid", idx),
                    cloud_cover_high_pct=_series_value(hourly, "cloud_cover_high", idx),
                    precipitation_mm=_series_value(hourly, "precipitation", idx),
                    shortwave_radiation_wm2=_series_value(hourly, "shortwave_radiation", idx),
                    wind_speed_10m_kt=_series_value(hourly, "wind_speed_10m", idx),
                    wind_direction_10m_deg=_series_value(hourly, "wind_direction_10m", idx),
                    pressure_msl_hpa=_series_value(hourly, "pressure_msl", idx),
                    surface_pressure_hpa=_series_value(hourly, "surface_pressure", idx),
                    cape_jkg=_series_value(hourly, "cape", idx),
                    soil_moisture_0_to_1cm_m3m3=_series_value(hourly, "soil_moisture_0_to_7cm", idx),
                )
            )
        hourly_tmax = [_series_value(hourly, "temperature_2m_max", idx) for idx, ts in enumerate(times) if str(ts).startswith(target_date.isoformat())]
        temperatures = [point.temperature_2m_c for point in points if point.temperature_2m_c is not None]
        tmax_candidates = [value for value in hourly_tmax if value is not None] or temperatures
        return ModelForecast(
            model="ecmwf_hres_9km",
            available=bool(tmax_candidates),
            target_date=target_date,
            hourly=points,
            tmax_c=float(max(tmax_candidates)) if tmax_candidates else None,
            unavailable_reason=None if tmax_candidates else "ECMWF HRES returned no LTAC temperature points for target date",
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            await self.get_model_forecast(datetime.now(ZoneInfo(self.settings.report_timezone)).date())
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _series_value(hourly: dict[str, Any], key: str, idx: int) -> float | None:
    series = hourly.get(key) or []
    if idx >= len(series):
        return None
    value = series[idx]
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
