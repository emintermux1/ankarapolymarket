from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import SourceHealth, SourceState


class UVIndexAdapter(HttpSource):
    """UV Index adapter using Open-Meteo (free, no API key needed)."""

    source_name = "UV_Index"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def get_uv(self) -> dict[str, Any]:
        """Get current and daily max UV index for Ankara."""
        url = "https://api.open-meteo.com/v1/forecast"
        payload = await self._request_json(
            url,
            params={
                "latitude": self.settings.ltac_latitude,
                "longitude": self.settings.ltac_longitude,
                "daily": "uv_index_max,uv_index_clear_sky_max",
                "timezone": self.settings.report_timezone,
                "forecast_days": 1,
            },
        )
        if not isinstance(payload, dict):
            return {}
        daily = payload.get("daily")
        if not isinstance(daily, dict):
            return {}
        uv_max_values = daily.get("uv_index_max") or []
        uv_clear_values = daily.get("uv_index_clear_sky_max") or []
        return {
            "uv_index_max": float(uv_max_values[0]) if uv_max_values else None,
            "uv_clear_sky_max": float(uv_clear_values[0]) if uv_clear_values else None,
            "date": (daily.get("time") or [None])[0],
            "fetch_timestamp": datetime.now(timezone.utc),
        }

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            data = await self.get_uv()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if data.get("uv_index_max") is not None:
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message="UV verisi alındı ancak değer yok")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))
