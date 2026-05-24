from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import numpy as np

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import RadarMotionSignal, SourceHealth, SourceState


class RainViewerRadarAdapter(HttpSource):
    source_name = "RainViewer"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.api_url = "https://api.rainviewer.com/public/weather-maps.json"
        self.tile_size = 256
        self.zoom = 7

    async def get_radar_motion(self, wind_direction_deg: int | None = None) -> RadarMotionSignal:
        payload = await self._request_json(self.api_url)
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "weather maps payload is not an object")
        frames = ((payload.get("radar") or {}).get("past") or [])
        if not frames:
            raise SourceError(self.source_name, "radar frames are unavailable")
        host = str(payload.get("host") or "").rstrip("/")
        if not host:
            raise SourceError(self.source_name, "radar tile host is unavailable")

        latest = frames[-1]
        previous = frames[-2] if len(frames) >= 2 else None
        latest_tile = await self._request_bytes(self._tile_url(host, latest))
        previous_tile = await self._request_bytes(self._tile_url(host, previous)) if previous else None
        latest_regions = _sample_regions(latest_tile)
        previous_regions = _sample_regions(previous_tile) if previous_tile else {}
        upwind_region = _wind_region(wind_direction_deg, latest_regions)
        downwind_region = _opposite_region(upwind_region)

        center = latest_regions.get("center")
        previous_center = previous_regions.get("center")
        upwind = latest_regions.get(upwind_region)
        downwind = latest_regions.get(downwind_region)
        max_nearby = max([value for key, value in latest_regions.items() if key != "center"], default=None)
        motion = _motion_label(center, previous_center, upwind, downwind, max_nearby)
        confidence = _motion_confidence(center, upwind, downwind, max_nearby)

        return RadarMotionSignal(
            fetch_timestamp=datetime.now(timezone.utc),
            frame_time=_frame_time(latest),
            previous_frame_time=_frame_time(previous) if previous else None,
            center_intensity=center,
            previous_center_intensity=previous_center,
            upwind_intensity=upwind,
            downwind_intensity=downwind,
            max_nearby_intensity=max_nearby,
            motion=motion,
            confidence=confidence,
            raw_json={
                "latest_frame": latest,
                "previous_frame": previous,
                "upwind_region": upwind_region,
                "regions": latest_regions,
            },
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            payload = await self._request_json(self.api_url)
            frames = ((payload or {}).get("radar") or {}).get("past") if isinstance(payload, dict) else None
            if not frames:
                return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, message="radar frames unavailable")
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))

    def _tile_url(self, host: str, frame: dict[str, Any] | None) -> str:
        path = str((frame or {}).get("path") or "").strip()
        if not path:
            raise SourceError(self.source_name, "radar frame path is unavailable")
        lat = f"{self.settings.ltac_latitude:.4f}"
        lon = f"{self.settings.ltac_longitude:.4f}"
        return f"{host}{path}/{self.tile_size}/{self.zoom}/{lat}/{lon}/2/1_1.png"


def _sample_regions(png_bytes: bytes) -> dict[str, float]:
    import matplotlib.image as mpimg

    image = mpimg.imread(BytesIO(png_bytes))
    array = np.asarray(image, dtype=float)
    if array.max(initial=0) > 1:
        array = array / 255.0
    h, w = array.shape[:2]
    cx, cy = w // 2, h // 2
    regions = {
        "center": (slice(cy - 22, cy + 22), slice(cx - 22, cx + 22)),
        "n": (slice(18, 92), slice(cx - 36, cx + 36)),
        "ne": (slice(30, 104), slice(w - 104, w - 30)),
        "e": (slice(cy - 36, cy + 36), slice(w - 92, w - 18)),
        "se": (slice(h - 104, h - 30), slice(w - 104, w - 30)),
        "s": (slice(h - 92, h - 18), slice(cx - 36, cx + 36)),
        "sw": (slice(h - 104, h - 30), slice(30, 104)),
        "w": (slice(cy - 36, cy + 36), slice(18, 92)),
        "nw": (slice(30, 104), slice(30, 104)),
    }
    return {key: round(_intensity_index(array[ys, xs]), 2) for key, (ys, xs) in regions.items()}


def _intensity_index(region: np.ndarray) -> float:
    if region.size == 0:
        return 0.0
    if region.ndim == 3 and region.shape[2] >= 4:
        alpha = region[..., 3]
        return float(np.clip(alpha.mean() * 100.0, 0.0, 100.0))
    if region.ndim == 3:
        brightness = region[..., :3].mean(axis=2)
    else:
        brightness = region
    return float(np.clip((brightness > 0.04).mean() * 100.0, 0.0, 100.0))


def _wind_region(wind_direction_deg: int | None, regions: dict[str, float]) -> str:
    if wind_direction_deg is None:
        candidates = {key: value for key, value in regions.items() if key != "center"}
        return max(candidates, key=candidates.get, default="w")
    direction = wind_direction_deg % 360
    sectors = [
        ("n", 337.5, 360.0),
        ("n", 0.0, 22.5),
        ("ne", 22.5, 67.5),
        ("e", 67.5, 112.5),
        ("se", 112.5, 157.5),
        ("s", 157.5, 202.5),
        ("sw", 202.5, 247.5),
        ("w", 247.5, 292.5),
        ("nw", 292.5, 337.5),
    ]
    return next(region for region, lower, upper in sectors if lower <= direction < upper)


def _opposite_region(region: str) -> str:
    return {
        "n": "s",
        "ne": "sw",
        "e": "w",
        "se": "nw",
        "s": "n",
        "sw": "ne",
        "w": "e",
        "nw": "se",
    }.get(region, "e")


def _motion_label(
    center: float | None,
    previous_center: float | None,
    upwind: float | None,
    downwind: float | None,
    max_nearby: float | None,
) -> str:
    center = center or 0.0
    previous_center = previous_center or 0.0
    upwind = upwind or 0.0
    downwind = downwind or 0.0
    max_nearby = max_nearby or 0.0
    if max(center, max_nearby) < 1.0:
        return "no_echo"
    if center >= 7.0:
        if center >= previous_center + 3.0:
            return "intensifying_overhead"
        return "overhead"
    if upwind >= center + 4.0 and upwind >= downwind:
        return "approaching"
    if downwind >= center + 4.0 and downwind >= upwind:
        return "departing"
    return "nearby"


def _motion_confidence(
    center: float | None,
    upwind: float | None,
    downwind: float | None,
    max_nearby: float | None,
) -> float:
    values = [value for value in (center, upwind, downwind, max_nearby) if value is not None]
    if not values or max(values) < 1.0:
        return 0.35
    gradient = abs((upwind or 0.0) - (downwind or 0.0))
    return round(max(0.45, min(0.9, 0.45 + gradient / 80.0 + (center or 0.0) / 120.0)), 2)


def _frame_time(frame: dict[str, Any]) -> datetime | None:
    value = frame.get("time")
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc)
