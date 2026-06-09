from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class WindyAdapter(HttpSource):
    source_name = "Windy"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.windy.com/api/point-forecast/v2"

    def _headers(self) -> dict[str, str]:
        if not self.settings.windy_api_key:
            raise SourceError(self.source_name, "WINDY_API_KEY not configured")
        return {"X-WINDY-API-KEY": self.settings.windy_api_key}

    async def get_point_forecast(self, target_date: date | None = None) -> dict[str, Any]:
        """Fetch Windy point forecast for LTAC coordinates."""
        return await self._request_json(
            self.base_url,
            params={
                "lat": self.settings.ltac_latitude,
                "lon": self.settings.ltac_longitude,
                "model": "gfs",
            },
            headers=self._headers(),
        )

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        """Convert Windy forecast to ModelForecast."""
        try:
            payload = await self.get_point_forecast(target_date)
        except SourceError as exc:
            return ModelForecast(
                model="windy",
                available=False,
                target_date=target_date,
                unavailable_reason=str(exc),
            )
        if not isinstance(payload, dict):
            return ModelForecast(
                model="windy",
                available=False,
                target_date=target_date,
                unavailable_reason="Windy payload is not an object",
            )
        tz = ZoneInfo(self.settings.report_timezone)
        points: list[ModelHourlyPoint] = []
        ts_list = payload.get("ts") or []
        temp_surface = payload.get("temp-surface") or []
        rh = payload.get("rh-2m") or []
        wind_u = payload.get("wind_u-surface") or []
        wind_v = payload.get("wind_v-surface") or []
        precip = payload.get("precip") or []
        mslp = payload.get("mslp") or []

        import math

        for idx, ts_val in enumerate(ts_list):
            if not ts_val or not isinstance(ts_val, (int, float)):
                continue
            raw_ts = int(ts_val)
            try:
                ts = datetime.fromtimestamp(raw_ts / 1000, tz=timezone.utc).astimezone(tz)
            except (OSError, ValueError):
                continue
            if ts.date() != target_date:
                continue
            u = _get_idx(wind_u, idx)
            v = _get_idx(wind_v, idx)
            wind_speed = None
            wind_dir = None
            if u is not None and v is not None:
                wind_speed = math.sqrt(u * u + v * v) * 1.943844
                wind_dir = (math.degrees(math.atan2(u, v)) + 360) % 360
            point = ModelHourlyPoint(
                time=ts,
                temperature_2m_c=_get_idx(temp_surface, idx),
                relative_humidity_pct=_get_idx(rh, idx),
                wind_speed_10m_kt=wind_speed,
                wind_direction_10m_deg=wind_dir,
                precipitation_mm=_get_idx(precip, idx),
                pressure_msl_hpa=_get_idx(mslp, idx),
            )
            if point.temperature_2m_c is not None:
                points.append(point)

        if not points:
            return ModelForecast(
                model="windy",
                available=False,
                target_date=target_date,
                unavailable_reason="Windy: no temperature data for target date",
                raw_model_key_map={"temperature_2m": "temp-surface"},
            )
        temperatures = [p.temperature_2m_c for p in points if p.temperature_2m_c is not None]
        return ModelForecast(
            model="windy",
            available=True,
            target_date=target_date,
            hourly=points,
            tmax_c=max(temperatures) if temperatures else None,
            raw_model_key_map={
                "temperature_2m": "temp-surface",
                "relative_humidity_2m": "rh-2m",
                "wind_speed_10m": "wind_u-surface/wind_v-surface",
                "precipitation": "precip",
                "pressure_msl": "mslp",
            },
        )

    async def get_radar_image_url(self) -> str:
        """Return URL for current Windy radar image for Ankara region."""
        return (
            f"https://embed.windy.com/embed2.html?"
            f"lat={self.settings.ltac_latitude}&lon={self.settings.ltac_longitude}"
            f"&zoom=8&level=surface&overlay=radar"
        )

    async def get_satellite_image_url(self) -> str:
        """Return URL for current Windy satellite image for Ankara region."""
        return (
            f"https://embed.windy.com/embed2.html?"
            f"lat={self.settings.ltac_latitude}&lon={self.settings.ltac_longitude}"
            f"&zoom=8&level=surface&overlay=satellite"
        )

    async def health(self) -> SourceHealth:
        if not self.settings.windy_api_key:
            return SourceHealth(
                source=self.source_name,
                state=SourceState.UNAVAILABLE,
                message="WINDY_API_KEY not configured",
            )
        started = datetime.now(timezone.utc)
        try:
            await self.get_point_forecast()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _get_idx(lst: list[Any], idx: int) -> float | None:
    if idx < len(lst) and lst[idx] is not None:
        try:
            return float(lst[idx])
        except (TypeError, ValueError):
            return None
    return None


def unavailable_health() -> SourceHealth:
    """Backward-compatible unavailable health for legacy callers."""
    return SourceHealth(
        source="Windy",
        state=SourceState.UNAVAILABLE,
        message="WINDY_API_KEY not configured",
    )
