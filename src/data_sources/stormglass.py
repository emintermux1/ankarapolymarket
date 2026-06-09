from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class StormglassAdapter(HttpSource):
    """Stormglass.io marine weather API adapter (optional, requires API key)."""

    source_name = "Stormglass"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def get_model_forecast(self, target_date: date) -> ModelForecast | None:
        if not self.settings.stormglass_api_key:
            return None
        try:
            payload = await self._request_json(
                "https://api.stormglass.io/v2/weather/point",
                params={
                    "lat": self.settings.ltac_latitude,
                    "lng": self.settings.ltac_longitude,
                    "params": "airTemperature",
                    "start": target_date.isoformat(),
                    "end": target_date.isoformat(),
                },
                headers={"Authorization": self.settings.stormglass_api_key},
            )
            if not isinstance(payload, dict):
                return None
            hours = payload.get("hours") or []
            points = []
            for hour in hours:
                if not isinstance(hour, dict):
                    continue
                temp_data = hour.get("airTemperature")
                if not isinstance(temp_data, dict):
                    continue
                temp = _safe_float(temp_data.get("sg"))
                if temp is None:
                    continue
                time_str = hour.get("time")
                if time_str:
                    try:
                        ts = datetime.fromisoformat(str(time_str).replace("Z", "+00:00"))
                    except ValueError:
                        ts = datetime.now(timezone.utc)
                else:
                    ts = datetime.now(timezone.utc)
                points.append(ModelHourlyPoint(time=ts, temperature_2m_c=temp))
            if not points:
                return None
            temps = [p.temperature_2m_c for p in points if p.temperature_2m_c is not None]
            return ModelForecast(
                model="stormglass",
                available=True,
                target_date=target_date,
                hourly=points,
                tmax_c=max(temps) if temps else None,
            )
        except Exception:
            return None

    async def health(self) -> SourceHealth:
        if not self.settings.stormglass_api_key:
            return SourceHealth(source=self.source_name, state=SourceState.UNAVAILABLE, message="STORMGLASS_API_KEY not configured")
        started = datetime.now(timezone.utc)
        try:
            target = date.today()
            result = await self.get_model_forecast(target)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if result is not None:
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message="Stormglass forecast returned no data")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
