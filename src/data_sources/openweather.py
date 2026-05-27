from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class OpenWeatherAdapter(HttpSource):
    source_name = "OpenWeather"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.forecast_url = "https://api.openweathermap.org/data/2.5/forecast"

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        if not self.settings.openweather_api_key:
            raise SourceError(self.source_name, "OPENWEATHER_API_KEY not configured")
        payload = await self._request_json(
            self.forecast_url,
            params={
                "lat": self.settings.ltac_latitude,
                "lon": self.settings.ltac_longitude,
                "appid": self.settings.openweather_api_key,
                "units": "metric",
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "payload is not an object")
        tz = ZoneInfo(self.settings.report_timezone)
        points: list[ModelHourlyPoint] = []
        for item in payload.get("list") or []:
            if not isinstance(item, dict):
                continue
            ts = _parse_time(item.get("dt_txt") or item.get("dt"), tz)
            if ts is None or ts.date() != target_date:
                continue
            main = item.get("main") if isinstance(item.get("main"), dict) else {}
            wind = item.get("wind") if isinstance(item.get("wind"), dict) else {}
            clouds = item.get("clouds") if isinstance(item.get("clouds"), dict) else {}
            rain = item.get("rain") if isinstance(item.get("rain"), dict) else {}
            point = ModelHourlyPoint(
                time=ts,
                temperature_2m_c=_safe_float(main.get("temp")),
                relative_humidity_pct=_safe_float(main.get("humidity")),
                pressure_msl_hpa=_safe_float(main.get("pressure")),
                wind_speed_10m_kt=_ms_to_kt(wind.get("speed")),
                wind_direction_10m_deg=_safe_float(wind.get("deg")),
                cloud_cover_pct=_safe_float(clouds.get("all")),
                precipitation_mm=_safe_float(rain.get("3h")),
            )
            if point.temperature_2m_c is not None:
                points.append(point)
        if not points:
            return ModelForecast(
                model="openweather",
                available=False,
                target_date=target_date,
                unavailable_reason="OpenWeather target-date 3h forecast unavailable",
            )
        temperatures = [point.temperature_2m_c for point in points if point.temperature_2m_c is not None]
        return ModelForecast(
            model="openweather",
            available=True,
            target_date=target_date,
            hourly=points,
            tmax_c=max(temperatures) if temperatures else None,
            raw_model_key_map={
                "temperature_2m": "list[].main.temp",
                "relative_humidity_2m": "list[].main.humidity",
                "cloud_cover": "list[].clouds.all",
                "precipitation": "list[].rain.3h",
                "wind_speed_10m": "list[].wind.speed",
            },
        )

    async def health(self) -> SourceHealth:
        if not self.settings.openweather_api_key:
            return SourceHealth(source=self.source_name, state=SourceState.UNAVAILABLE, message="OPENWEATHER_API_KEY not configured")
        started = datetime.now(timezone.utc)
        try:
            await self.get_model_forecast(datetime.now(ZoneInfo(self.settings.report_timezone)).date())
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _parse_time(value: Any, tz: ZoneInfo) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(tz)
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=tz)
    except ValueError:
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _ms_to_kt(value: Any) -> float | None:
    raw = _safe_float(value)
    if raw is None:
        return None
    return raw * 1.943844
