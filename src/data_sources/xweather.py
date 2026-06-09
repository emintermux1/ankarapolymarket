from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class XWeatherAdapter(HttpSource):
    source_name = "xWeather"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.aerisapi.com/forecasts"

    def _auth_params(self) -> dict[str, Any]:
        if not self.settings.xweather_client_id or not self.settings.xweather_client_secret:
            raise SourceError(self.source_name, "XWEATHER_CLIENT_ID or XWEATHER_CLIENT_SECRET not configured")
        return {
            "client_id": self.settings.xweather_client_id,
            "client_secret": self.settings.xweather_client_secret,
        }

    async def get_forecast(self, target_date: date | None = None) -> dict[str, Any]:
        """Fetch xWeather (Aeris) forecast for LTAC coordinates."""
        params = self._auth_params()
        params["from"] = (target_date or date.today()).isoformat()
        if target_date:
            params["to"] = target_date.isoformat()
        url = f"{self.base_url}/{self.settings.ltac_latitude},{self.settings.ltac_longitude}"
        return await self._request_json(url, params=params)

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        """Convert xWeather forecast to ModelForecast."""
        try:
            payload = await self.get_forecast(target_date)
        except SourceError as exc:
            return ModelForecast(
                model="xweather",
                available=False,
                target_date=target_date,
                unavailable_reason=str(exc),
            )
        if not isinstance(payload, dict):
            return ModelForecast(
                model="xweather",
                available=False,
                target_date=target_date,
                unavailable_reason="xWeather payload is not an object",
            )
        response_data = payload.get("response")
        if not isinstance(response_data, list) or not response_data:
            return ModelForecast(
                model="xweather",
                available=False,
                target_date=target_date,
                unavailable_reason="xWeather response list is empty",
            )
        first = response_data[0]
        if not isinstance(first, dict):
            return ModelForecast(
                model="xweather",
                available=False,
                target_date=target_date,
                unavailable_reason="xWeather response entry is not an object",
            )
        periods = first.get("periods") or []
        if not isinstance(periods, list):
            periods = []

        tz = ZoneInfo(self.settings.report_timezone)
        points: list[ModelHourlyPoint] = []
        for period in periods:
            if not isinstance(period, dict):
                continue
            ts_iso = period.get("dateTimeISO") or period.get("timestamp")
            if ts_iso:
                try:
                    ts = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00")).astimezone(tz)
                except ValueError:
                    continue
            else:
                continue
            if ts.date() != target_date:
                continue
            temp = _safe_float(period.get("maxTempC") or period.get("tempC"))
            humidity = _safe_float(period.get("humidity"))
            wind_speed = period.get("windSpeedKPH")
            wind_dir = period.get("windDirDEG") or period.get("windDir")
            pressure = _safe_float(period.get("pressureMB"))
            cloud_cover = _safe_float(period.get("cloudsCoded") or period.get("sky"))
            precip = _safe_float(period.get("precipMM"))
            tmax_c = _safe_float(period.get("maxTempC"))
            point = ModelHourlyPoint(
                time=ts,
                temperature_2m_c=tmax_c or temp,
                relative_humidity_pct=humidity,
                wind_speed_10m_kt=_kmh_to_kt(wind_speed),
                wind_direction_10m_deg=_safe_float(wind_dir),
                pressure_msl_hpa=pressure,
                cloud_cover_pct=cloud_cover,
                precipitation_mm=precip,
            )
            if point.temperature_2m_c is not None:
                points.append(point)

        if not points:
            return ModelForecast(
                model="xweather",
                available=False,
                target_date=target_date,
                unavailable_reason="xWeather: no data for target date",
                raw_model_key_map={"temperature_2m": "response[].periods[].maxTempC"},
            )
        temperatures = [p.temperature_2m_c for p in points if p.temperature_2m_c is not None]
        return ModelForecast(
            model="xweather",
            available=True,
            target_date=target_date,
            hourly=points,
            tmax_c=max(temperatures) if temperatures else None,
            raw_model_key_map={
                "temperature_2m": "periods[].maxTempC",
                "relative_humidity_2m": "periods[].humidity",
                "cloud_cover": "periods[].cloudsCoded",
                "precipitation": "periods[].precipMM",
            },
        )

    async def health(self) -> SourceHealth:
        if not self.settings.xweather_client_id or not self.settings.xweather_client_secret:
            return SourceHealth(
                source=self.source_name,
                state=SourceState.UNAVAILABLE,
                message="XWEATHER_CLIENT_ID or XWEATHER_CLIENT_SECRET not configured",
            )
        started = datetime.now(timezone.utc)
        try:
            await self.get_forecast()
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
