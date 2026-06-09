from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class StormglassAdapter(HttpSource):
    source_name = "Stormglass"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.stormglass.io/v2/weather/point"

    def _headers(self) -> dict[str, str]:
        if not self.settings.stormglass_api_key:
            raise SourceError(self.source_name, "STORMGLASS_API_KEY not configured")
        return {"Authorization": self.settings.stormglass_api_key}

    async def get_point_forecast(self, target_date: date | None = None) -> dict[str, Any]:
        """Fetch Stormglass point forecast.

        Params: airTemperature, humidity, windSpeed, windDirection, pressure,
                cloudCover, precipitation.
        """
        params: dict[str, Any] = {
            "lat": self.settings.ltac_latitude,
            "lng": self.settings.ltac_longitude,
            "params": "airTemperature,humidity,windSpeed,windDirection,pressure,cloudCover,precipitation",
        }
        if target_date:
            params["start"] = target_date.isoformat()
            params["end"] = target_date.isoformat()

        return await self._request_json(
            self.base_url,
            params=params,
            headers=self._headers(),
        )

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        """Convert Stormglass data to ModelForecast for engine compatibility."""
        try:
            payload = await self.get_point_forecast(target_date)
        except SourceError as exc:
            return ModelForecast(
                model="stormglass",
                available=False,
                target_date=target_date,
                unavailable_reason=str(exc),
            )
        if not isinstance(payload, dict):
            return ModelForecast(
                model="stormglass",
                available=False,
                target_date=target_date,
                unavailable_reason="Stormglass payload is not an object",
            )
        hours = payload.get("hours") or []
        if not isinstance(hours, list) or not hours:
            return ModelForecast(
                model="stormglass",
                available=False,
                target_date=target_date,
                unavailable_reason="Stormglass returned no hourly data",
            )
        tz = ZoneInfo(self.settings.report_timezone)
        points: list[ModelHourlyPoint] = []
        for entry in hours:
            if not isinstance(entry, dict):
                continue
            ts_str = entry.get("time")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")).astimezone(tz)
            except ValueError:
                continue
            if ts.date() != target_date:
                continue
            air_temp = self._extract_sg(entry, "airTemperature")
            humidity = self._extract_sg(entry, "humidity")
            wind_speed = self._extract_sg(entry, "windSpeed")
            wind_dir = self._extract_sg(entry, "windDirection")
            pressure = self._extract_sg(entry, "pressure")
            cloud_cover = self._extract_sg(entry, "cloudCover")
            precip = self._extract_sg(entry, "precipitation")
            point = ModelHourlyPoint(
                time=ts,
                temperature_2m_c=air_temp,
                relative_humidity_pct=humidity,
                wind_speed_10m_kt=_ms_to_kt(wind_speed),
                wind_direction_10m_deg=wind_dir,
                pressure_msl_hpa=pressure,
                cloud_cover_pct=cloud_cover,
                precipitation_mm=precip,
            )
            if point.temperature_2m_c is not None:
                points.append(point)
        if not points:
            return ModelForecast(
                model="stormglass",
                available=False,
                target_date=target_date,
                unavailable_reason="Stormglass: no temperature data for target date",
                raw_model_key_map={"temperature_2m": "hours[].airTemperature"},
            )
        temperatures = [p.temperature_2m_c for p in points if p.temperature_2m_c is not None]
        return ModelForecast(
            model="stormglass",
            available=True,
            target_date=target_date,
            hourly=points,
            tmax_c=max(temperatures) if temperatures else None,
            raw_model_key_map={
                "temperature_2m": "hours[].airTemperature.sg",
                "relative_humidity_2m": "hours[].humidity.sg",
                "wind_speed_10m": "hours[].windSpeed.sg",
                "pressure_msl": "hours[].pressure.sg",
                "cloud_cover": "hours[].cloudCover.sg",
                "precipitation": "hours[].precipitation.sg",
            },
        )

    @staticmethod
    def _extract_sg(entry: dict[str, Any], key: str) -> float | None:
        """Stormglass wraps values in {source: value} dicts; try sg first."""
        val = entry.get(key)
        if isinstance(val, dict):
            return _safe_float(val.get("sg") or val.get("noaa") or val.get("icon"))
        return _safe_float(val)

    async def health(self) -> SourceHealth:
        if not self.settings.stormglass_api_key:
            return SourceHealth(
                source=self.source_name,
                state=SourceState.UNAVAILABLE,
                message="STORMGLASS_API_KEY not configured",
            )
        started = datetime.now(timezone.utc)
        try:
            await self.get_point_forecast()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ms_to_kt(value: Any) -> float | None:
    raw = _safe_float(value)
    if raw is None:
        return None
    return raw * 1.943844
