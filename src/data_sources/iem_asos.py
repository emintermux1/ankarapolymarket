from __future__ import annotations

import csv
import math
from datetime import date, datetime, time, timezone
from io import StringIO
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import ActualResult, SourceHealth, SourceState


class IEMASOSAdapter(HttpSource):
    source_name = "IEM_ASOS"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

    async def fetch_history(self, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
        payload = await self._request_text(
            self.url,
            params={
                "station": self.settings.ltac_icao,
                "network": "TR__ASOS",
                "data": "tmpc,dwpc,relh,drct,sknt,p01m,alti,mslp,vsby,skyc1,skyc2,skyc3,skyl1,skyl2,skyl3,wxcodes,metar",
                "year1": start_at.year,
                "month1": start_at.month,
                "day1": start_at.day,
                "hour1": start_at.hour,
                "minute1": start_at.minute,
                "year2": end_at.year,
                "month2": end_at.month,
                "day2": end_at.day,
                "hour2": end_at.hour,
                "minute2": end_at.minute,
                "tz": self.settings.report_timezone,
                "format": "onlycomma",
                "latlon": "yes",
                "direct": "yes",
                "report_type": "3,4",
            },
        )
        reader = csv.DictReader(StringIO(payload))
        return [row for row in reader]

    async def get_daily_actual(self, target_date: date) -> ActualResult:
        tz = ZoneInfo(self.settings.report_timezone)
        start_at = datetime.combine(target_date, time.min, tzinfo=tz)
        end_at = datetime.combine(target_date, time.max.replace(hour=23, minute=59), tzinfo=tz)
        rows = await self.fetch_history(start_at, end_at)
        return _actual_result_from_rows(target_date, rows, self.source_name)

    async def get_intraday_high(self, target_date: date, as_of: datetime | None = None) -> ActualResult:
        tz = ZoneInfo(self.settings.report_timezone)
        now_local = (as_of or datetime.now(timezone.utc)).astimezone(tz)
        start_at = datetime.combine(target_date, time.min, tzinfo=tz)
        if target_date == now_local.date():
            end_at = now_local
        else:
            end_at = datetime.combine(target_date, time.max.replace(hour=23, minute=59), tzinfo=tz)
        rows = await self.fetch_history(start_at, end_at)
        return _actual_result_from_rows(target_date, rows, f"{self.source_name}_intraday")

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            today = datetime.now(ZoneInfo(self.settings.report_timezone)).date()
            await self.get_daily_actual(today)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _actual_result_from_rows(target_date: date, rows: list[dict[str, Any]], source: str) -> ActualResult:
    values = []
    for row in rows:
        raw_value = row.get("tmpc")
        if raw_value in (None, "", "M"):
            continue
        try:
            values.append((float(raw_value), row))
        except ValueError:
            continue
    if not values:
        return ActualResult(
            target_date=target_date,
            source=source,
            fetched_at=datetime.now(timezone.utc),
            unavailable_reason="IEM ASOS returned no LTAC temperature observations",
        )
    tmax, max_row = max(values, key=lambda item: item[0])
    return ActualResult(
        target_date=target_date,
        source=source,
        fetched_at=datetime.now(timezone.utc),
        tmax_c=tmax,
        rounded_tmax_c=_reported_integer_temperature(tmax),
        raw_payload={
            "rows": rows,
            "observation_count": len(values),
            "max_observation_time": max_row.get("valid"),
            "max_metar": max_row.get("metar"),
        },
    )


def _reported_integer_temperature(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)
