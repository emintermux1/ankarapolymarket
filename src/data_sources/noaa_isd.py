from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from io import StringIO
from typing import Any

import httpx

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import ActualResult, SourceHealth, SourceState, round_market_temperature_c


class NOAAISDAdapter(HttpSource):
    source_name = "NOAA_ISD"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://www.ncei.noaa.gov/data/global-hourly/access"

    async def fetch_year(self, year: int) -> list[dict[str, Any]]:
        payload = await self._request_text(f"{self.base_url}/{year}/{self.settings.noaa_isd_station_file}")
        return list(csv.DictReader(StringIO(payload)))

    async def get_daily_actual(self, target_date: date) -> ActualResult:
        rows = [row for row in await self.fetch_year(target_date.year) if str(row.get("DATE", "")).startswith(target_date.isoformat())]
        values = []
        for row in rows:
            temp = _parse_isd_temperature(row.get("TMP"))
            if temp is not None:
                values.append((temp, row))
        if not values:
            return ActualResult(
                target_date=target_date,
                source=self.source_name,
                fetched_at=datetime.now(timezone.utc),
                unavailable_reason="NOAA ISD returned no LTAC temperature observations for target date",
            )
        tmax, max_row = max(values, key=lambda item: item[0])
        return ActualResult(
            target_date=target_date,
            source=self.source_name,
            fetched_at=datetime.now(timezone.utc),
            tmax_c=tmax,
            rounded_tmax_c=round_market_temperature_c(tmax),
            raw_payload={"observation_count": len(values), "max_observation_time": max_row.get("DATE")},
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            current_year = datetime.now(timezone.utc).year
            year = await self._latest_available_year(current_year)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency, message=f"{year} archive reachable")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))

    async def _latest_available_year(self, current_year: int) -> int:
        async with httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds,
            headers={"User-Agent": "ankara-ltac-weather-bot/0.1"},
            follow_redirects=True,
        ) as client:
            for year in (current_year, current_year - 1, current_year - 2):
                response = await client.head(f"{self.base_url}/{year}/{self.settings.noaa_isd_station_file}")
                if response.status_code == 200:
                    return year
            response.raise_for_status()
        raise RuntimeError("NOAA ISD station archive not found")


def _parse_isd_temperature(value: Any) -> float | None:
    if value in (None, "", "+9999,9", "9999,9"):
        return None
    try:
        raw, quality = str(value).split(",", 1)
        if quality and quality[0] not in {"0", "1", "4", "5", "9", "A", "C", "I"}:
            return None
        integer = int(raw)
    except (TypeError, ValueError):
        return None
    if abs(integer) >= 9999:
        return None
    return integer / 10.0
