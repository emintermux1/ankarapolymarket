from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class WeatherbitAdapter(HttpSource):
    source_name = "Weatherbit"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.forecast_url = "https://api.weatherbit.io/v2.0/forecast/daily"

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        if not self.settings.weatherbit_api_key:
            raise SourceError(self.source_name, "WEATHERBIT_API_KEY not configured")
        payload = await self._request_json(
            self.forecast_url,
            params={
                "lat": self.settings.ltac_latitude,
                "lon": self.settings.ltac_longitude,
                "key": self.settings.weatherbit_api_key,
                "days": 16,
                "units": "M",
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "payload is not an object")
        target_row = None
        for item in payload.get("data") or []:
            if isinstance(item, dict) and item.get("valid_date") == target_date.isoformat():
                target_row = item
                break
        if target_row is None:
            return ModelForecast(
                model="weatherbit",
                available=False,
                target_date=target_date,
                unavailable_reason="Weatherbit target date unavailable",
            )
        tmax = _safe_float(target_row.get("max_temp"))
        if tmax is None:
            return ModelForecast(
                model="weatherbit",
                available=False,
                target_date=target_date,
                unavailable_reason="Weatherbit max_temp unavailable",
                raw_model_key_map={"tempmax": "data[].max_temp"},
            )
        tz = ZoneInfo(self.settings.report_timezone)
        point = ModelHourlyPoint(
            time=datetime.combine(target_date, time(hour=15), tzinfo=tz),
            temperature_2m_c=tmax,
            relative_humidity_pct=_safe_float(target_row.get("rh")),
            cloud_cover_pct=_safe_float(target_row.get("clouds")),
            precipitation_mm=_safe_float(target_row.get("precip")),
            wind_speed_10m_kt=_ms_to_kt(target_row.get("wind_spd")),
            wind_direction_10m_deg=_safe_float(target_row.get("wind_dir")),
        )
        return ModelForecast(
            model="weatherbit",
            available=True,
            target_date=target_date,
            hourly=[point],
            tmax_c=tmax,
            raw_model_key_map={
                "temperature_2m": "data[].max_temp",
                "relative_humidity_2m": "data[].rh",
                "cloud_cover": "data[].clouds",
                "precipitation": "data[].precip",
                "wind_speed_10m": "data[].wind_spd",
            },
        )

    async def health(self) -> SourceHealth:
        if not self.settings.weatherbit_api_key:
            return SourceHealth(source=self.source_name, state=SourceState.UNAVAILABLE, message="WEATHERBIT_API_KEY not configured")
        started = datetime.now(timezone.utc)
        try:
            await self.get_model_forecast(datetime.now(ZoneInfo(self.settings.report_timezone)).date())
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _ms_to_kt(value: Any) -> float | None:
    raw = _safe_float(value)
    if raw is None:
        return None
    return raw * 1.943844
