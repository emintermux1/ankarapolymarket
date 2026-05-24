from __future__ import annotations

import re
from datetime import date, datetime, timezone

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import ActualResult, SourceHealth, SourceState, round_market_temperature_c


class WundergroundScraper(HttpSource):
    source_name = "Wunderground"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    def daily_url(self, target_date: date) -> str:
        return f"https://www.wunderground.com/history/daily/tr/%C3%A7ubuk/LTAC/date/{target_date.year}-{target_date.month}-{target_date.day}"

    async def get_daily_result(self, target_date: date) -> ActualResult:
        url = self.daily_url(target_date)
        html = await self._request_text(url)
        parsed = self._parse_temperature_high(html)
        if parsed is None:
            return ActualResult(
                target_date=target_date,
                source=self.source_name,
                fetched_at=datetime.now(timezone.utc),
                raw_payload={"url": url, "html_sample": html[:500]},
                unavailable_reason="Wunderground page did not expose finalized daily high in static HTML",
                manual_required=True,
            )
        return ActualResult(
            target_date=target_date,
            source=self.source_name,
            fetched_at=datetime.now(timezone.utc),
            tmax_c=parsed,
            rounded_tmax_c=round_market_temperature_c(parsed),
            raw_payload={"url": url},
        )

    async def health(self) -> SourceHealth:
        try:
            await self.get_daily_result(date.today())
            return SourceHealth(
                source=self.source_name,
                state=SourceState.DEGRADED,
                message="static fetch works; final result may require manual confirmation without Weather.com API key",
            )
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))

    @staticmethod
    def _parse_temperature_high(html: str) -> float | None:
        patterns = [
            r'"temperatureHigh"\s*:\s*([+-]?\d+(?:\.\d+)?)',
            r'"tempHigh"\s*:\s*([+-]?\d+(?:\.\d+)?)',
            r"High Temperature[^0-9+-]*([+-]?\d+(?:\.\d+)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, html, flags=re.IGNORECASE)
            if match:
                return float(match.group(1))
        return None
