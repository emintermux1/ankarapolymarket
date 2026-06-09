from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class HerbieAdapter(HttpSource):
    source_name = "Herbie"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.openmeteo_url = "https://api.open-meteo.com/v1/forecast"

    async def get_gfs_tmax(self, target_date: date) -> float | None:
        """Fetch GFS tmax for LTAC from NOAA API (JSON fallback)."""
        forecast = await self.get_model_forecast(target_date)
        return forecast.tmax_c

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        """Fetch GFS model data via Open-Meteo global API.

        Uses Open-Meteo's GFS seamless model which provides global coverage
        including LTAC/Ankara coordinates. This replaces the US-only NWS
        gridpoint API that cannot resolve non-US locations.
        """
        lat = self.settings.ltac_latitude
        lon = self.settings.ltac_longitude
        tz = ZoneInfo(self.settings.report_timezone)

        try:
            payload = await self._request_json(
                self.openmeteo_url,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m",
                    "daily": "temperature_2m_max",
                    "models": "gfs_seamless",
                    "timezone": self.settings.report_timezone,
                    "start_date": target_date.isoformat(),
                    "end_date": target_date.isoformat(),
                },
            )
        except SourceError as exc:
            return ModelForecast(
                model="gfs_herbie",
                available=False,
                target_date=target_date,
                unavailable_reason=str(exc),
            )

        if not isinstance(payload, dict):
            return ModelForecast(
                model="gfs_herbie",
                available=False,
                target_date=target_date,
                unavailable_reason="Open-Meteo GFS payload is not an object",
            )

        hourly = payload.get("hourly") if isinstance(payload.get("hourly"), dict) else {}
        daily = payload.get("daily") if isinstance(payload.get("daily"), dict) else {}
        times = hourly.get("time") or []
        temps = hourly.get("temperature_2m") or []
        humidities = hourly.get("relative_humidity_2m") or []
        wind_speeds = hourly.get("wind_speed_10m") or []
        wind_dirs = hourly.get("wind_direction_10m") or []
        daily_max = daily.get("temperature_2m_max") or []

        points: list[ModelHourlyPoint] = []
        for idx, ts_str in enumerate(times):
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_str)).replace(tzinfo=tz)
            except ValueError:
                continue
            if ts.date() != target_date:
                continue
            wind_speed_ms = _get_list_value(wind_speeds, idx)
            point = ModelHourlyPoint(
                time=ts,
                temperature_2m_c=_get_list_value(temps, idx),
                relative_humidity_pct=_get_list_value(humidities, idx),
                wind_speed_10m_kt=_ms_to_kt(wind_speed_ms),
                wind_direction_10m_deg=_get_list_value(wind_dirs, idx),
            )
            if point.temperature_2m_c is not None:
                points.append(point)

        daily_tmax = _get_list_value(daily_max, 0) if daily_max else None
        hourly_temps = [p.temperature_2m_c for p in points if p.temperature_2m_c is not None]
        tmax = daily_tmax if daily_tmax is not None else (max(hourly_temps) if hourly_temps else None)

        return ModelForecast(
            model="gfs_herbie",
            available=tmax is not None,
            target_date=target_date,
            hourly=points,
            tmax_c=tmax,
            raw_model_key_map={
                "temperature_2m": "hourly.temperature_2m",
                "wind_speed_10m": "hourly.wind_speed_10m",
                "wind_direction_10m": "hourly.wind_direction_10m",
                "relative_humidity_2m": "hourly.relative_humidity_2m",
            },
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            forecast = await self.get_model_forecast(datetime.now(ZoneInfo(self.settings.report_timezone)).date())
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if forecast.available:
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(
                source=self.source_name,
                state=SourceState.DEGRADED,
                latency_ms=latency,
                message=forecast.unavailable_reason or "GFS data not available",
            )
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _get_list_value(lst: list[Any], idx: int) -> float | None:
    if idx < len(lst) and lst[idx] is not None:
        try:
            return float(lst[idx])
        except (TypeError, ValueError):
            return None
    return None


def _ms_to_kt(value: float | None) -> float | None:
    if value is None:
        return None
    return value * 1.94384
