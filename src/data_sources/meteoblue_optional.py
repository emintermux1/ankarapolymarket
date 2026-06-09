from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class MeteoblueAdapter(HttpSource):
    source_name = "Meteoblue"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://my.meteoblue.com/packages"

    def _check_key(self) -> None:
        if not self.settings.meteoblue_api_key:
            raise SourceError(self.source_name, "METEOBLUE_API_KEY not configured")

    async def get_meteogram(self) -> dict[str, Any]:
        """Fetch Meteoblue meteogram data for LTAC coordinates."""
        self._check_key()
        params: dict[str, Any] = {
            "apikey": self.settings.meteoblue_api_key,
            "lat": self.settings.ltac_latitude,
            "lon": self.settings.ltac_longitude,
            "format": "json",
        }
        return await self._request_json(
            f"{self.base_url}/basic-day",
            params=params,
        )

    async def get_forecast(self, target_date: date) -> dict[str, Any]:
        """Fetch Meteoblue forecast for target_date."""
        self._check_key()
        params: dict[str, Any] = {
            "apikey": self.settings.meteoblue_api_key,
            "lat": self.settings.ltac_latitude,
            "lon": self.settings.ltac_longitude,
            "format": "json",
        }
        return await self._request_json(
            f"{self.base_url}/basic-day",
            params=params,
        )

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        """Convert Meteoblue forecast to ModelForecast."""
        try:
            payload = await self.get_forecast(target_date)
        except SourceError as exc:
            return ModelForecast(
                model="meteoblue",
                available=False,
                target_date=target_date,
                unavailable_reason=str(exc),
            )
        if not isinstance(payload, dict):
            return ModelForecast(
                model="meteoblue",
                available=False,
                target_date=target_date,
                unavailable_reason="Meteoblue payload is not an object",
            )
        tz = ZoneInfo(self.settings.report_timezone)
        points: list[ModelHourlyPoint] = []
        data_day = payload.get("data_day") if isinstance(payload.get("data_day"), dict) else {}
        times = data_day.get("time") or []

        temperature_max = data_day.get("temperature_max") or []
        temperature_min = data_day.get("temperature_min") or []
        precipitation = data_day.get("precipitation") or []
        windspeed = data_day.get("windspeed_mean") or []

        for idx, ts_str in enumerate(times):
            if not ts_str or not str(ts_str).startswith(target_date.isoformat()):
                continue
            try:
                ts = datetime.fromisoformat(str(ts_str)).replace(tzinfo=tz)
            except ValueError:
                continue
            point = ModelHourlyPoint(
                time=ts,
                temperature_2m_c=_get_idx(temperature_max, idx),
                precipitation_mm=_get_idx(precipitation, idx),
                wind_speed_10m_kt=_get_idx(windspeed, idx),
            )
            if point.temperature_2m_c is not None:
                points.append(point)

        if not points:
            return ModelForecast(
                model="meteoblue",
                available=False,
                target_date=target_date,
                unavailable_reason="Meteoblue: no data for target date",
                raw_model_key_map={"temperature_2m": "data_day.temperature_max"},
            )
        temperatures = [p.temperature_2m_c for p in points if p.temperature_2m_c is not None]
        return ModelForecast(
            model="meteoblue",
            available=True,
            target_date=target_date,
            hourly=points,
            tmax_c=max(temperatures) if temperatures else None,
            raw_model_key_map={
                "temperature_2m": "data_day.temperature_max",
                "precipitation": "data_day.precipitation",
                "wind_speed_10m": "data_day.windspeed_mean",
            },
        )

    async def health(self) -> SourceHealth:
        if not self.settings.meteoblue_api_key:
            return SourceHealth(
                source=self.source_name,
                state=SourceState.UNAVAILABLE,
                message="METEOBLUE_API_KEY not configured",
            )
        started = datetime.now(timezone.utc)
        try:
            await self.get_meteogram()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _get_idx(lst: list[Any], idx: int) -> float | None:
    if idx < len(lst) and lst[idx] is not None:
        try:
            return float(lst[idx])
        except (TypeError, ValueError):
            return None
    return None
