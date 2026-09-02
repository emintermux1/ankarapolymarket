from __future__ import annotations

from datetime import datetime, timezone

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import SourceHealth, SourceState


class RainViewerAdapter(HttpSource):
    source_name = "RainViewer"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.url = "https://api.rainviewer.com/public/weather-maps.json"

    async def latest_radar_tile_url(self, zoom: int = 7, size: int = 512) -> str:
        payload = await self._request_json(self.url)
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "Radar payload is not an object")
        frames = ((payload.get("radar") or {}).get("past") or [])
        if not frames:
            raise SourceError(self.source_name, "No radar frames returned")
        frame = frames[-1]
        host = payload.get("host")
        path = frame.get("path") if isinstance(frame, dict) else None
        if not host or not path:
            raise SourceError(self.source_name, "Radar frame is missing host/path")
        return f"{host}{path}/{size}/{zoom}/{self.settings.ltac_latitude:.4f}/{self.settings.ltac_longitude:.4f}/2/1_1.png"

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            await self.latest_radar_tile_url()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))
