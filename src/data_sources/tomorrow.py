from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class TomorrowIOAdapter(HttpSource):
    source_name = "Tomorrow.io"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.forecast_url = "https://api.tomorrow.io/v4/weather/forecast"

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        if not self.settings.tomorrow_api_key:
            raise SourceError(self.source_name, "TOMORROW_API_KEY not configured")
        payload = await self._request_json(
            self.forecast_url,
            params={
                "location": f"{self.settings.ltac_latitude},{self.settings.ltac_longitude}",
                "apikey": self.settings.tomorrow_api_key,
                "timesteps": "1h",
                "units": "metric",
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "payload is not an object")
        hourly = ((payload.get("timelines") or {}).get("hourly") or [])
        points = []
        for item in hourly:
            if not isinstance(item, dict):
                continue
            ts = _parse_time(item.get("time"))
            values = item.get("values") or {}
            if ts is None or ts.date() != target_date or not isinstance(values, dict):
                continue
            temperature = _safe_float(values.get("temperature"))
            cloud_cover = _safe_float(values.get("cloudCover"))
            cloud_base_m = _km_to_m(values.get("cloudBase"))
            cloud_ceiling_m = _km_to_m(values.get("cloudCeiling"))
            point = ModelHourlyPoint(
                time=ts,
                temperature_2m_c=temperature,
                cloud_cover_pct=cloud_cover,
                cloud_cover_low_pct=_layer_cover(cloud_cover, cloud_base_m, 0, 3000),
                cloud_cover_mid_pct=_layer_cover(cloud_cover, cloud_base_m, 3000, 8000),
                cloud_cover_high_pct=_layer_cover(cloud_cover, cloud_base_m, 8000, None),
                wind_speed_10m_kt=_safe_float(values.get("windSpeed")),
                wind_direction_10m_deg=_safe_float(values.get("windDirection")),
                shortwave_radiation_wm2=_safe_float(values.get("solarGHI")),
                cloud_base_m=cloud_base_m,
                cloud_ceiling_m=cloud_ceiling_m,
            )
            if point.temperature_2m_c is not None:
                points.append(point)
        if not points:
            return ModelForecast(
                model="tomorrow_io",
                available=False,
                target_date=target_date,
                unavailable_reason="Tomorrow.io hourly target-date temperatures unavailable",
            )
        temperatures = [point.temperature_2m_c for point in points if point.temperature_2m_c is not None]
        return ModelForecast(
            model="tomorrow_io",
            available=True,
            target_date=target_date,
            hourly=points,
            tmax_c=max(temperatures) if temperatures else None,
            raw_model_key_map={
                "temperature_2m": "values.temperature",
                "cloud_cover": "values.cloudCover",
                "cloud_base": "values.cloudBase",
                "cloud_ceiling": "values.cloudCeiling",
                "shortwave_radiation": "values.solarGHI",
            },
        )

    async def health(self) -> SourceHealth:
        if not self.settings.tomorrow_api_key:
            return SourceHealth(source=self.source_name, state=SourceState.UNAVAILABLE, message="TOMORROW_API_KEY not configured")
        started = datetime.now(timezone.utc)
        try:
            await self.get_model_forecast(datetime.now(timezone.utc).date())
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _km_to_m(value: Any) -> float | None:
    raw = _safe_float(value)
    if raw is None:
        return None
    return raw * 1000


def _layer_cover(cloud_cover: float | None, cloud_base_m: float | None, lower_m: int, upper_m: int | None) -> float | None:
    if cloud_cover is None or cloud_base_m is None:
        return None
    if cloud_base_m < lower_m:
        return None
    if upper_m is not None and cloud_base_m >= upper_m:
        return None
    return cloud_cover
