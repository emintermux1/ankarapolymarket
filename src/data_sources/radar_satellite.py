from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import httpx

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import SourceHealth, SourceState


class RadarSatelliteAdapter(HttpSource):
    """Radar and satellite imagery adapter using Open-Meteo/other free sources."""

    source_name = "Radar_Satellite"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    def radar_url(self) -> str:
        return self.settings.radar_motion_url

    def satellite_url(self) -> str:
        return self.settings.satellite_motion_url

    async def get_radar_image(self) -> tuple[str, str] | None:
        """Download radar image, return (file_path, caption)."""
        url = "https://api.open-meteo.com/v1/forecast"
        # We use the Windy radar map snapshot as a static image fallback
        static_url = f"https://embed.windy.com/embed2.html?lat={self.settings.ltac_latitude}&lon={self.settings.ltac_longitude}&detailLat={self.settings.ltac_latitude}&detailLon={self.settings.ltac_longitude}&width=800&height=600&zoom=9&level=surface&overlay=radar&product=ecmwf&menu=&message=&marker=&calendar=now&pressure=&type=map&location=coordinates&detail=&metricWind=kt&metricTemp=%C2%B0C&radarRange=-1"
        return static_url, f"Ankara radar görüntüsü - {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"

    async def get_satellite_image(self) -> tuple[str, str] | None:
        """Download satellite image, return (file_path, caption)."""
        static_url = f"https://embed.windy.com/embed2.html?lat={self.settings.ltac_latitude}&lon={self.settings.ltac_longitude}&detailLat={self.settings.ltac_latitude}&detailLon={self.settings.ltac_longitude}&width=800&height=600&zoom=9&level=surface&overlay=satellite&product=ecmwf&menu=&message=&marker=&calendar=now&pressure=&type=map&location=coordinates&detail=&metricWind=kt&metricTemp=%C2%B0C&radarRange=-1"
        return static_url, f"Ankara uydu görüntüsü - {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            url = f"https://embed.windy.com/embed2.html?lat={self.settings.ltac_latitude}&lon={self.settings.ltac_longitude}&zoom=9&overlay=radar"
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers={"User-Agent": "ankara-ltac-weather-bot/0.1"},
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))
