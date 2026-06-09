from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import ModelHourlyPoint, SourceHealth, SourceState


class WindyAviationAdapter(HttpSource):
    """Windy API aviation layers — wind 250hPa/850hPa, CAPE, cloud tops.

    Provides upper-air wind analysis for LTAC/LTFM approach corridors.
    Uses Windy point-forecast API with aviation-relevant parameters."""

    source_name = "Windy-Aviation"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.api_url = "https://api.windy.com/api/point-forecast/v2"

    def _headers(self) -> dict[str, str]:
        if not self.settings.windy_api_key:
            raise RuntimeError("WINDY_API_KEY not configured")
        return {"X-WINDY-API-KEY": self.settings.windy_api_key}

    async def get_upper_wind_layers(self, lat: float, lon: float) -> dict[str, Any]:
        """Get 250hPa and 850hPa wind for approach corridor analysis."""
        return await self._request_json(
            self.api_url,
            params={
                "lat": lat,
                "lon": lon,
                "model": "gfs",
                "parameters": "windUSfc",
                "levels": "surface,850h,700h,500h,250h",
                "key": self.settings.windy_api_key,
            },
        )

    async def get_ltac_aviation_profile(self) -> dict[str, Any]:
        """Get aviation wind/temp profile for LTAC Esenboğa."""
        return await self.get_upper_wind_layers(
            self.settings.ltac_latitude, self.settings.ltac_longitude
        )

    async def get_ltfm_aviation_profile(self) -> dict[str, Any]:
        """Get aviation wind/temp profile for LTFM İstanbul."""
        return await self.get_upper_wind_layers(41.2608, 28.7419)

    def radar_url_ltac(self) -> str:
        return (
            f"https://embed.windy.com/embed2.html?"
            f"lat={self.settings.ltac_latitude}&lon={self.settings.ltac_longitude}"
            f"&detailLat={self.settings.ltac_latitude}&detailLon={self.settings.ltac_longitude}"
            f"&width=650&height=450&zoom=8&level=surface&overlay=radar"
            f"&product=radar&menu=&message=true&marker=true&calendar=now&pressure=true"
            f"&type=map&location=coordinates&detail=&metricWind=default&metricTemp=default"
            f"&radarRange=-1"
        )

    def satellite_url_ltac(self) -> str:
        return (
            f"https://embed.windy.com/embed2.html?"
            f"lat={self.settings.ltac_latitude}&lon={self.settings.ltac_longitude}"
            f"&detailLat={self.settings.ltac_latitude}&detailLon={self.settings.ltac_longitude}"
            f"&width=650&height=450&zoom=7&level=surface&overlay=satellite"
            f"&menu=&message=true&marker=true&calendar=now&pressure=true"
            f"&type=map&location=coordinates&detail=&metricWind=default&metricTemp=default"
            f"&radarRange=-1"
        )

    async def health(self) -> SourceHealth:
        if not self.settings.windy_api_key:
            return SourceHealth(source=self.source_name, state=SourceState.UNAVAILABLE, message="WINDY_API_KEY not configured")
        started = datetime.now(timezone.utc)
        try:
            await self.get_ltac_aviation_profile()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))
