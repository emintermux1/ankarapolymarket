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
        self.noaa_api = "https://api.weather.gov"
        self.nomads_url = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"

    async def get_gfs_tmax(self, target_date: date) -> float | None:
        """Fetch GFS tmax for LTAC from NOAA API (JSON fallback)."""
        forecast = await self.get_model_forecast(target_date)
        return forecast.tmax_c

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        """Fetch GFS model data via NOAA public API.

        Uses the NOAA NWS public API for grid-based forecasts as a fallback
        since direct GRIB file parsing requires the herbie-data package.
        """
        lat = self.settings.ltac_latitude
        lon = self.settings.ltac_longitude

        try:
            points_payload = await self._request_json(
                f"{self.noaa_api}/points/{lat},{lon}",
            )
        except SourceError as exc:
            return ModelForecast(
                model="gfs_noaa",
                available=False,
                target_date=target_date,
                unavailable_reason=str(exc),
            )

        if not isinstance(points_payload, dict):
            return ModelForecast(
                model="gfs_noaa",
                available=False,
                target_date=target_date,
                unavailable_reason="NOAA points payload is not an object",
            )

        forecast_url = None
        properties = points_payload.get("properties") if isinstance(points_payload.get("properties"), dict) else {}
        if isinstance(properties, dict):
            forecast_url = properties.get("forecast")
        if not forecast_url:
            return ModelForecast(
                model="gfs_noaa",
                available=False,
                target_date=target_date,
                unavailable_reason="NOAA forecast URL not found in response",
            )

        try:
            forecast_payload = await self._request_json(str(forecast_url))
        except SourceError as exc:
            return ModelForecast(
                model="gfs_noaa",
                available=False,
                target_date=target_date,
                unavailable_reason=str(exc),
            )

        if not isinstance(forecast_payload, dict):
            return ModelForecast(
                model="gfs_noaa",
                available=False,
                target_date=target_date,
                unavailable_reason="NOAA forecast payload is not an object",
            )

        tz = ZoneInfo(self.settings.report_timezone)
        periods = (
            forecast_payload.get("properties", {}).get("periods", [])
            if isinstance(forecast_payload.get("properties"), dict)
            else []
        )
        if not isinstance(periods, list):
            periods = []

        points: list[ModelHourlyPoint] = []
        for period in periods:
            if not isinstance(period, dict):
                continue
            start_time_str = period.get("startTime")
            if not start_time_str:
                continue
            try:
                ts = datetime.fromisoformat(str(start_time_str).replace("Z", "+00:00")).astimezone(tz)
            except ValueError:
                continue
            if ts.date() != target_date:
                continue
            temp_f = _safe_float(period.get("temperature"))
            temp_c = _f_to_c(temp_f) if temp_f is not None else None
            wind_speed_str = period.get("windSpeed", "")
            wind_speed_kt = _parse_wind_speed(wind_speed_str)
            wind_dir_deg = _parse_wind_direction(period.get("windDirection", ""))
            humidity = _safe_float(period.get("relativeHumidity", {}).get("value") if isinstance(period.get("relativeHumidity"), dict) else None)

            point = ModelHourlyPoint(
                time=ts,
                temperature_2m_c=temp_c,
                relative_humidity_pct=humidity,
                wind_speed_10m_kt=wind_speed_kt,
                wind_direction_10m_deg=wind_dir_deg,
                shortwave_radiation_wm2=None,
            )
            if point.temperature_2m_c is not None:
                points.append(point)

        if not points:
            return ModelForecast(
                model="gfs_noaa",
                available=False,
                target_date=target_date,
                unavailable_reason="NOAA GFS: no data for target date",
                raw_model_key_map={"temperature_2m": "properties.periods[].temperature"},
            )
        temperatures = [p.temperature_2m_c for p in points if p.temperature_2m_c is not None]
        return ModelForecast(
            model="gfs_noaa",
            available=True,
            target_date=target_date,
            hourly=points,
            tmax_c=max(temperatures) if temperatures else None,
            raw_model_key_map={
                "temperature_2m": "periods[].temperature",
                "wind_speed_10m": "periods[].windSpeed",
                "wind_direction_10m": "periods[].windDirection",
                "relative_humidity_2m": "periods[].relativeHumidity.value",
            },
        )

    async def health(self) -> SourceHealth:
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
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _f_to_c(value: float | None) -> float | None:
    if value is None:
        return None
    return (value - 32.0) * 5.0 / 9.0


def _parse_wind_speed(text: str) -> float | None:
    import re
    if not text:
        return None
    match = re.search(r"(\d+)\s*mph", text.lower())
    if match:
        return float(match.group(1)) * 0.868976
    match = re.search(r"(\d+)\s*kt", text.lower())
    if match:
        return float(match.group(1))
    return None


def _parse_wind_direction(text: str) -> float | None:
    import re
    if not text:
        return None
    match = re.search(r"(\d+)\s*°", text)
    if match:
        return float(match.group(1))
    dirs = {
        "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5,
        "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5,
        "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
        "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
    }
    return dirs.get(text.strip().upper()) if text else None
