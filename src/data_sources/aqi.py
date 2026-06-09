from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import SourceHealth, SourceState


class AQIAdapter(HttpSource):
    """Air Quality Index adapter using Open-Meteo Air Quality API (free, no key needed)."""

    source_name = "AQI"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def get_aqi(self) -> dict[str, Any]:
        """Get current European AQI for Ankara coordinates."""
        url = "https://air-quality-api.open-meteo.com/v1/air-quality"
        payload = await self._request_json(
            url,
            params={
                "latitude": self.settings.aqi_latitude,
                "longitude": self.settings.aqi_longitude,
                "current": "european_aqi,us_aqi,pm2_5,pm10,nitrogen_dioxide,sulphur_dioxide,ozone,carbon_monoxide",
            },
        )
        if not isinstance(payload, dict):
            return {}
        current = payload.get("current")
        if not isinstance(current, dict):
            return {}
        return {
            "european_aqi": _safe_float(current.get("european_aqi")),
            "us_aqi": _safe_float(current.get("us_aqi")),
            "pm2_5": _safe_float(current.get("pm2_5")),
            "pm10": _safe_float(current.get("pm10")),
            "no2": _safe_float(current.get("nitrogen_dioxide")),
            "so2": _safe_float(current.get("sulphur_dioxide")),
            "o3": _safe_float(current.get("ozone")),
            "co": _safe_float(current.get("carbon_monoxide")),
            "fetch_timestamp": datetime.now(timezone.utc),
        }

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            data = await self.get_aqi()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if data.get("european_aqi") is not None:
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message="AQI verisi alındı ancak değer yok")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
