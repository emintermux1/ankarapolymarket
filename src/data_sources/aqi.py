from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import AQISnapshot, SourceHealth, SourceState


AQI_LATITUDE = 39.9334
AQI_LONGITUDE = 32.8597


class AQIAdapter(HttpSource):
    source_name = "OpenWeather-AQI"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "http://api.openweathermap.org/data/2.5/air_pollution"

    async def get_current_aqi(self) -> dict[str, Any]:
        """Fetch current air quality index for Ankara.

        Returns dict with aqi_index, pm25, pm10, o3, no2, so2, co, dt.
        """
        if not self.settings.openweather_api_key:
            raise SourceError(self.source_name, "OPENWEATHER_API_KEY not configured")
        payload = await self._request_json(
            self.base_url,
            params={
                "lat": AQI_LATITUDE,
                "lon": AQI_LONGITUDE,
                "appid": self.settings.openweather_api_key,
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "AQI payload is not an object")
        entries = payload.get("list")
        if not isinstance(entries, list) or not entries:
            raise SourceError(self.source_name, "AQI list is empty")
        row = entries[0]
        if not isinstance(row, dict):
            raise SourceError(self.source_name, "AQI entry is not an object")
        components = row.get("components") if isinstance(row.get("components"), dict) else {}
        return {
            "aqi_index": int(row.get("main", {}).get("aqi", 1)),
            "pm25": _safe_float(components.get("pm2_5")),
            "pm10": _safe_float(components.get("pm10")),
            "o3": _safe_float(components.get("o3")),
            "no2": _safe_float(components.get("no2")),
            "so2": _safe_float(components.get("so2")),
            "co": _safe_float(components.get("co")),
            "dt": row.get("dt"),
        }

    async def get_forecast_aqi(self) -> list[dict[str, Any]]:
        """Fetch AQI forecast list."""
        if not self.settings.openweather_api_key:
            raise SourceError(self.source_name, "OPENWEATHER_API_KEY not configured")
        payload = await self._request_json(
            f"{self.base_url}/forecast",
            params={
                "lat": AQI_LATITUDE,
                "lon": AQI_LONGITUDE,
                "appid": self.settings.openweather_api_key,
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "AQI forecast payload is not an object")
        entries = payload.get("list")
        if not isinstance(entries, list):
            return []
        results: list[dict[str, Any]] = []
        for row in entries:
            if not isinstance(row, dict):
                continue
            components = row.get("components") if isinstance(row.get("components"), dict) else {}
            results.append({
                "aqi_index": int(row.get("main", {}).get("aqi", 1)),
                "pm25": _safe_float(components.get("pm2_5")),
                "pm10": _safe_float(components.get("pm10")),
                "o3": _safe_float(components.get("o3")),
                "no2": _safe_float(components.get("no2")),
                "so2": _safe_float(components.get("so2")),
                "co": _safe_float(components.get("co")),
                "dt": row.get("dt"),
            })
        return results

    async def get_aqi_snapshot(self) -> AQISnapshot | None:
        """Return a fully typed AQISnapshot."""
        try:
            data = await self.get_current_aqi()
        except SourceError:
            return None
        return AQISnapshot(
            fetch_timestamp=datetime.now(timezone.utc),
            latitude=AQI_LATITUDE,
            longitude=AQI_LONGITUDE,
            aqi_index=data.get("aqi_index", 1),
            pm25=data.get("pm25"),
            pm10=data.get("pm10"),
            o3=data.get("o3"),
            no2=data.get("no2"),
            so2=data.get("so2"),
            co=data.get("co"),
            raw_json=data,
        )

    async def health(self) -> SourceHealth:
        if not self.settings.openweather_api_key:
            return SourceHealth(
                source=self.source_name,
                state=SourceState.UNAVAILABLE,
                message="OPENWEATHER_API_KEY not configured",
            )
        started = datetime.now(timezone.utc)
        try:
            await self.get_current_aqi()
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
