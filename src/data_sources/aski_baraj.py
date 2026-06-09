from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import BarajSnapshot, SourceHealth, SourceState


class ASKIBarajAdapter(HttpSource):
    source_name = "ASKİ_Baraj"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def get_baraj_doluluk(self) -> dict[str, float | None]:
        """Return {dam_name: fill_percentage, ...} for Ankara dams."""
        html = await self._request_text(self.settings.aski_baraj_url)
        return _parse_baraj_table(html)

    async def get_ankara_baraj_summary(self) -> dict[str, Any]:
        """Return total fill %, daily change, last_updated."""
        html = await self._request_text(self.settings.aski_baraj_url)
        dams = _parse_baraj_table(html)
        total_pct = None
        daily_change = None
        if dams:
            fills = [v for v in dams.values() if v is not None]
            if fills:
                total_pct = round(sum(fills) / len(fills), 1)
        last_updated = _parse_last_updated(html)
        return {
            "total_fill_pct": total_pct,
            "daily_change_pct": daily_change,
            "last_updated": last_updated,
            "dam_count": len(dams),
        }

    async def get_snapshot(self) -> BarajSnapshot:
        fetch_timestamp = datetime.now(timezone.utc)
        html = await self._request_text(self.settings.aski_baraj_url)
        dams = _parse_baraj_table(html)
        dams_list = [{"name": name, "fill_pct": value} for name, value in dams.items()]
        summary = await self.get_ankara_baraj_summary()
        return BarajSnapshot(
            fetch_timestamp=fetch_timestamp,
            total_fill_pct=summary["total_fill_pct"],
            daily_change_pct=summary["daily_change_pct"],
            dams=dams_list,
            last_updated=summary.get("last_updated"),
            raw_text=html[:2000],
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            html = await self._request_text(self.settings.aski_baraj_url)
            dams = _parse_baraj_table(html)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if dams:
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message="baraj verisi parse edilemedi")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


_ANKARA_DAMS = [
    "Çamlıdere", "Kurtboğazı", "Eğrekkaya", "Akyar",
    "Çubuk 1", "Çubuk 2", "Kayabaşı", "Kesikköprü",
    "Tatlar", "Uludere", "Kurtboğazı Barajı",
]


def _parse_baraj_table(html: str) -> dict[str, float | None]:
    """Extract dam fill percentages from ASKİ HTML."""
    dams: dict[str, float | None] = {}
    # Try percentage patterns like "Çamlıdere %45,2" or "45.2%"
    percent_pattern = re.compile(
        r"(%\s*)?(\d{1,3})[\.,](\d{0,2})\s*%?",
    )

    # Try to match dam names near percentages
    for dam in _ANKARA_DAMS:
        # Look for dam name followed by percentage within 200 chars
        pattern = re.compile(
            re.escape(dam) + r".{0,200}?" + r"(%?\s*)(\d{1,3})[\.,](\d{0,2})\s*%?",
            flags=re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(html)
        if match:
            try:
                integer_part = int(match.group(2))
                decimal_part = match.group(3)
                value = float(f"{integer_part}.{decimal_part}") if decimal_part else float(integer_part)
                # Validate reasonable range
                if 0 <= value <= 100:
                    dams[dam] = value
            except (ValueError, IndexError):
                continue

    # Fallback: try generic percentage extraction near "Ankara" or "baraj"
    if not dams:
        percent_matches = re.findall(r"(\d{1,3})[\.,](\d{1,2})\s*%", html)
        if percent_matches and len(percent_matches) >= 3:
            for i, (integer, decimal) in enumerate(percent_matches[:len(_ANKARA_DAMS)]):
                try:
                    value = float(f"{integer}.{decimal}")
                    if 0 <= value <= 100 and i < len(_ANKARA_DAMS):
                        dams[_ANKARA_DAMS[i]] = value
                except ValueError:
                    continue

    return dams


def _parse_last_updated(html: str) -> datetime | None:
    """Parse update timestamp from ASKİ page."""
    date_patterns = [
        r"(\d{2})[./\s](\d{2})[./\s](\d{4})",
        r"(\d{4})[./-](\d{2})[./-](\d{2})",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, html)
        if match:
            try:
                groups = match.groups()
                if len(groups[0]) == 4:
                    year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                else:
                    day, month, year = int(groups[0]), int(groups[1]), int(groups[2])
                return datetime(year, month, day, tzinfo=timezone.utc)
            except (ValueError, IndexError):
                continue
    return None
