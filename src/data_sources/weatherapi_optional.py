from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class WeatherAPIAdapter(HttpSource):
    source_name = "WeatherAPI"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.weatherapi.com/v1/forecast.json"

    def _check_key(self) -> None:
        if not self.settings.weatherapi_api_key:
            raise SourceError(self.source_name, "WEATHERAPI_API_KEY not configured")

    async def get_forecast(self, target_date: date) -> dict[str, Any]:
        """Fetch WeatherAPI forecast for LTAC coordinates."""
        self._check_key()
        return await self._request_json(
            self.base_url,
            params={
                "key": self.settings.weatherapi_api_key,
                "q": f"{self.settings.ltac_latitude},{self.settings.ltac_longitude}",
                "days": 3,
                "aqi": "no",
            },
        )

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        """Convert WeatherAPI forecast to ModelForecast."""
        try:
            payload = await self.get_forecast(target_date)
        except SourceError as exc:
            return ModelForecast(
                model="weatherapi",
                available=False,
                target_date=target_date,
                unavailable_reason=str(exc),
            )
        if not isinstance(payload, dict):
            return ModelForecast(
                model="weatherapi",
                available=False,
                target_date=target_date,
                unavailable_reason="WeatherAPI payload is not an object",
            )
        forecast = payload.get("forecast") if isinstance(payload.get("forecast"), dict) else {}
        forecast_days = forecast.get("forecastday") or []
        if not isinstance(forecast_days, list):
            forecast_days = []

        tz_str = payload.get("location", {}).get("tz_id") if isinstance(payload.get("location"), dict) else None
        tz = ZoneInfo(tz_str) if tz_str else ZoneInfo(self.settings.report_timezone)

        points: list[ModelHourlyPoint] = []
        target_str = target_date.isoformat()
        for day in forecast_days:
            if not isinstance(day, dict):
                continue
            if day.get("date") != target_str:
                continue
            hours = day.get("hour") or []
            if not isinstance(hours, list):
                continue
            for hour in hours:
                if not isinstance(hour, dict):
                    continue
                ts_str = hour.get("time")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(str(ts_str)).replace(tzinfo=tz)
                except ValueError:
                    continue
                point = ModelHourlyPoint(
                    time=ts,
                    temperature_2m_c=_safe_float(hour.get("temp_c")),
                    relative_humidity_pct=_safe_float(hour.get("humidity")),
                    wind_speed_10m_kt=_kmh_to_kt(hour.get("wind_kph")),
                    wind_direction_10m_deg=_safe_float(hour.get("wind_degree")),
                    pressure_msl_hpa=_safe_float(hour.get("pressure_mb")),
                    cloud_cover_pct=_safe_float(hour.get("cloud")),
                    precipitation_mm=_safe_float(hour.get("precip_mm")),
                    dew_point_2m_c=_safe_float(hour.get("dewpoint_c")),
                )
                if point.temperature_2m_c is not None:
                    points.append(point)
            break

        if not points:
            # Try day-level fallback
            day_obj = forecast_days[0] if forecast_days else {}
            day_data = day_obj.get("day") if isinstance(day_obj, dict) and day_obj.get("date") == target_str else {}
            if isinstance(day_data, dict):
                maxtemp = _safe_float(day_data.get("maxtemp_c"))
                if maxtemp is not None:
                    point = ModelHourlyPoint(
                        time=datetime.combine(target_date, datetime.min.time()).replace(tzinfo=tz),
                        temperature_2m_c=maxtemp,
                        relative_humidity_pct=_safe_float(day_data.get("avghumidity")),
                        wind_speed_10m_kt=_kmh_to_kt(day_data.get("maxwind_kph")),
                    )
                    points.append(point)
            if not points:
                return ModelForecast(
                    model="weatherapi",
                    available=False,
                    target_date=target_date,
                    unavailable_reason="WeatherAPI: no data for target date",
                    raw_model_key_map={"temperature_2m": "forecast.forecastday[].hour[].temp_c"},
                )

        temperatures = [p.temperature_2m_c for p in points if p.temperature_2m_c is not None]
        return ModelForecast(
            model="weatherapi",
            available=True,
            target_date=target_date,
            hourly=points,
            tmax_c=max(temperatures) if temperatures else None,
            raw_model_key_map={
                "temperature_2m": "forecastday[].hour[].temp_c",
                "relative_humidity_2m": "forecastday[].hour[].humidity",
                "wind_speed_10m": "forecastday[].hour[].wind_kph",
                "pressure_msl": "forecastday[].hour[].pressure_mb",
                "cloud_cover": "forecastday[].hour[].cloud",
                "precipitation": "forecastday[].hour[].precip_mm",
            },
        )

    async def health(self) -> SourceHealth:
        if not self.settings.weatherapi_api_key:
            return SourceHealth(
                source=self.source_name,
                state=SourceState.UNAVAILABLE,
                message="WEATHERAPI_API_KEY not configured",
            )
        started = datetime.now(timezone.utc)
        try:
            await self.get_forecast(datetime.now(ZoneInfo(self.settings.report_timezone)).date())
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


def _kmh_to_kt(value: Any) -> float | None:
    raw = _safe_float(value)
    if raw is None:
        return None
    return raw * 0.539957


def unavailable_health() -> SourceHealth:
    """Backward-compatible unavailable health for service.check_sources()."""
    return SourceHealth(
        source="WeatherAPI",
        state=SourceState.UNAVAILABLE,
        message="WEATHERAPI_API_KEY not configured",
    )
