from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import SourceHealth, SourceState


class RadarSatelliteAdapter(HttpSource):
    source_name = "Radar-Satellite"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.mgm_radar_base = "https://servis.mgm.gov.tr/web/radar"

    async def get_mgm_radar_url(self) -> str:
        """Return MGM radar image URL for Ankara region."""
        return f"{self.mgm_radar_base}/son-resim"

    async def get_windy_radar_url(self) -> str:
        """Return Windy radar motion URL from settings."""
        return self.settings.radar_motion_url

    async def get_satellite_url(self) -> str:
        """Return Windy satellite motion URL from settings."""
        return self.settings.satellite_motion_url

    async def download_radar_image(self, save_path: str) -> bool:
        """Download current MGM radar image to save_path. Returns True on success."""
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers={"User-Agent": "ankara-ltac-weather-bot/0.1"},
                follow_redirects=True,
            ) as client:
                response = await client.get(f"{self.mgm_radar_base}/son-resim")
                response.raise_for_status()
                with open(save_path, "wb") as f:
                    f.write(response.content)
            self.logger.info("Radar image saved to %s", save_path)
            return True
        except Exception as exc:
            self.logger.warning("Radar image download failed: %s", exc)
            return False

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            url = await self.get_mgm_radar_url()
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers={"User-Agent": "ankara-ltac-weather-bot/0.1"},
                follow_redirects=True,
            ) as client:
                response = await client.head(url)
                response.raise_for_status()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))
