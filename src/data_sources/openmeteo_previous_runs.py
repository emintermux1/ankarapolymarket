from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import SourceHealth, SourceState


class OpenMeteoPreviousRunsAdapter(HttpSource):
    source_name = "Open-Meteo Previous Runs"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.url = "https://previous-runs-api.open-meteo.com/v1/forecast"

    async def get_previous_day_temperatures(self, target_date: date, lead_days: int = 1) -> list[float]:
        variable = f"temperature_2m_previous_day{lead_days}"
        payload = await self._request_json(
            self.url,
            params={
                "latitude": self.settings.ltac_latitude,
                "longitude": self.settings.ltac_longitude,
                "hourly": variable,
                "models": ",".join(self.settings.openmeteo_models),
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
                "timezone": self.settings.report_timezone,
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "Previous-runs payload is not an object")
        hourly = payload.get("hourly") or {}
        values: list[float] = []
        for key, series in hourly.items():
            if not str(key).startswith(variable) or not isinstance(series, list):
                continue
            values.extend(float(value) for value in series if value is not None)
        return values

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            target = datetime.now(ZoneInfo(self.settings.report_timezone)).date() - timedelta(days=2)
            values = await self.get_previous_day_temperatures(target)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if values:
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message="previous run request succeeded but returned no temperature values")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))
