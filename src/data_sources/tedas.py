from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import PowerOutage, SourceHealth, SourceState


class TEDASAdapter(HttpSource):
    source_name = "TEDAŞ"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def get_ankara_outages(self) -> list[dict[str, Any]]:
        """Return list of {district, start_time, end_time, reason} for Ankara outages."""
        url = "https://www.tedas.gov.tr/tr/duyurular/planli-kesintiler"
        html = await self._request_text(url)
        return _parse_outages(html)

    async def get_outage_snapshots(self) -> list[PowerOutage]:
        fetch_timestamp = datetime.now(timezone.utc)
        outages = await self.get_ankara_outages()
        return [
            PowerOutage(
                fetch_timestamp=fetch_timestamp,
                district=item.get("district", "Ankara"),
                start_time=item.get("start_time"),
                end_time=item.get("end_time"),
                reason=item.get("reason"),
                affected_areas=item.get("affected_areas", []),
            )
            for item in outages
        ]

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            html = await self._request_text("https://www.tedas.gov.tr/tr/duyurular/planli-kesintiler")
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            outages = _parse_outages(html)
            if outages or "kesinti" in html.lower() or "elektrik" in html.lower():
                return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message=f"TEDAŞ sayfası erişilebilir ({len(outages)} kesinti parse edildi)")
            return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message="TEDAŞ sayfası erişilebilir ancak kesinti parse edilemedi")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


_ANKARA_DISTRICTS = [
    "Akyurt", "Altındağ", "Ayaş", "Bala", "Beypazarı", "Çamlıdere", "Çankaya",
    "Çubuk", "Elmadağ", "Etimesgut", "Evren", "Gölbaşı", "Güdül", "Haymana",
    "Kahramankazan", "Kalecik", "Keçiören", "Kızılcahamam", "Mamak", "Nallıhan",
    "Polatlı", "Pursaklar", "Sincan", "Şereflikoçhisar", "Yenimahalle",
]


def _parse_outages(html: str) -> list[dict[str, Any]]:
    """Parse TEDAŞ planned outage announcements."""
    outages: list[dict[str, Any]] = []
    # Look for Ankara-related sections
    ankara_section = _extract_ankara_section(html)

    # Try parsing table rows or list items
    for district in _ANKARA_DISTRICTS:
        district_pattern = re.compile(
            re.escape(district) + r"[:\s]*.*?(?=\n|$)",
            flags=re.IGNORECASE,
        )
        matches = district_pattern.findall(ankara_section if ankara_section else html)
        for match in matches:
            reason = _extract_reason(match)
            times = _extract_times(match)
            if reason or times:
                outages.append({
                    "district": district,
                    "start_time": times[0] if times else None,
                    "end_time": times[1] if len(times) > 1 else None,
                    "reason": reason,
                    "affected_areas": [],
                })

    # Fallback: generic Ankara outage detection
    if not outages and ("ankara" in html.lower() and ("kesinti" in html.lower() or "elektrik" in html.lower())):
        outages.append({
            "district": "Ankara (genel)",
            "start_time": None,
            "end_time": None,
            "reason": "TEDAŞ planlı kesinti duyurusu mevcut, detay parse edilemedi",
            "affected_areas": [],
        })

    return outages


def _extract_ankara_section(html: str) -> str | None:
    """Try to isolate the Ankara-specific portion of the page."""
    patterns = [
        r"(?i)ankara.*?(?=<h[2-4]|$)",
        r"(?i)ankara.{0,2000}",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.DOTALL)
        if match:
            return match.group(0)
    return None


def _extract_times(text: str) -> list[datetime | None]:
    """Extract start/end times from outage text."""
    time_pattern = re.compile(
        r"(\d{2}[/\.]\d{2}[/\.]\d{4})\s*(?:-|–|ile|ila|saat|arası)\s*(\d{2})[:\.](\d{2})\s*(?:-|–|ile|ila)\s*(\d{2})[:\.](\d{2})",
        flags=re.IGNORECASE,
    )
    match = time_pattern.search(text)
    if match:
        try:
            date_str = match.group(1).replace(".", "/")
            day, month = date_str.split("/")[:2]
            year = date_str.split("/")[2] if len(date_str.split("/")) > 2 else str(datetime.now().year)
            base = datetime(int(year), int(month), int(day))
            start = base.replace(hour=int(match.group(2)), minute=int(match.group(3)))
            end = base.replace(hour=int(match.group(4)), minute=int(match.group(5)))
            return [start, end]
        except (ValueError, IndexError):
            pass
    return []


def _extract_reason(text: str) -> str | None:
    """Extract reason from outage text."""
    reason_patterns = [
        r"(?i)(?:sebep|neden|gerekçe|açıklama)[:\s]+([^\n.]{5,100})",
        r"(?i)(bakım|onarım|çalışma|yatırım|proje)[^.]{0,50}",
    ]
    for pattern in reason_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None
