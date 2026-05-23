from __future__ import annotations

from datetime import date
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource


class OpenMeteoHistoricalForecastAdapter(HttpSource):
    source_name = "Open-Meteo-Historical"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.url = "https://historical-forecast-api.open-meteo.com/v1/forecast"

    async def fetch(self, start_date: date, end_date: date, models: list[str]) -> dict[str, Any]:
        payload = await self._request_json(
            self.url,
            params={
                "latitude": self.settings.ltac_latitude,
                "longitude": self.settings.ltac_longitude,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "hourly": "temperature_2m,precipitation,cloud_cover,wind_speed_10m",
                "models": ",".join(models),
                "timezone": self.settings.report_timezone,
            },
        )
        return payload if isinstance(payload, dict) else {"raw": payload}

