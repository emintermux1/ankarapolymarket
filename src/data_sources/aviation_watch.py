from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import AviationSourceSnapshot, SourceHealth, SourceState


SKYVECTOR_URLS = {
    "LTAC": "https://skyvector.com/airport/LTAC/Ankara-Esenboga-International-Airport",
    "LTFM": "https://skyvector.com/?ll=41.2608,28.7419&chart=301&zoom=4",
    "LTBA": "https://skyvector.com/airport/LTBA/Istanbul-Ataturk-Airport",
    "LTFJ": "https://skyvector.com/airport/LTFJ/Istanbul-Sabiha-Gokcen-International-Airport",
}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
}


class AviationWatchAdapter(HttpSource):
    source_name = "AviationWatch"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.aviapages_base_url = settings.aviapages_api_base_url.rstrip("/")

    async def fetch_snapshots(self) -> list[AviationSourceSnapshot]:
        stations = self.settings.aviation_source_watch_station_keys
        tasks = []
        for station in stations:
            tasks.append(self._safe(self.get_aviationweather_metar(station)))
            tasks.append(self._safe(self.get_aviationweather_taf(station)))
            tasks.append(self._safe(self.get_noaa_metar(station)))
            tasks.append(self._safe(self.get_noaa_taf(station)))
            tasks.append(self._safe(self.get_bigorre_notam(station)))
            if station in SKYVECTOR_URLS:
                tasks.append(self._safe(self.get_skyvector_airport(station)))
            tasks.append(self._safe(self.get_ifatc_airport(station)))
            tasks.append(self._safe(self.get_airnavradar_airport(station)))
            if self.settings.aviapages_api_token:
                tasks.append(self._safe(self.get_aviapages_airport(station)))
        results = await asyncio.gather(*tasks)
        return [snapshot for snapshot in results if snapshot is not None]

    async def get_aviationweather_metar(self, station: str) -> AviationSourceSnapshot:
        station_id = station.strip().upper()
        url = "https://aviationweather.gov/api/data/metar"
        payload = await self._request_json(url, params={"ids": station_id, "format": "json"})
        row = _first_payload_object(payload, "AviationWeather METAR")
        observed_at = _parse_aw_time(row.get("reportTime"), row.get("obsTime"))
        return _snapshot(
            source="AviationWeather",
            station=station_id,
            kind="official_metar_json",
            title=f"{station_id} AviationWeather official METAR",
            source_url=f"{url}?ids={station_id}&format=json",
            summary_lines=_aviationweather_metar_lines(row),
            observed_at=observed_at,
            raw_json=row,
            raw_text=json.dumps(row, sort_keys=True, ensure_ascii=False)[:4000],
        )

    async def get_aviationweather_taf(self, station: str) -> AviationSourceSnapshot:
        station_id = station.strip().upper()
        url = "https://aviationweather.gov/api/data/taf"
        payload = await self._request_json(url, params={"ids": station_id, "format": "json"})
        row = _first_payload_object(payload, "AviationWeather TAF")
        issued_at = _parse_iso(row.get("issueTime")) or _parse_iso(row.get("bulletinTime"))
        return _snapshot(
            source="AviationWeather",
            station=station_id,
            kind="official_taf_json",
            title=f"{station_id} AviationWeather official TAF",
            source_url=f"{url}?ids={station_id}&format=json",
            summary_lines=_aviationweather_taf_lines(row),
            observed_at=issued_at,
            raw_json=row,
            raw_text=json.dumps(row, sort_keys=True, ensure_ascii=False)[:4000],
        )

    async def get_noaa_metar(self, station: str) -> AviationSourceSnapshot:
        station_id = station.strip().upper()
        url = f"https://tgftp.nws.noaa.gov/data/observations/metar/stations/{station_id}.TXT"
        text = await self._request_text(url)
        return _noaa_snapshot(station_id, url, text)

    async def get_noaa_taf(self, station: str) -> AviationSourceSnapshot:
        station_id = station.strip().upper()
        url = f"https://tgftp.nws.noaa.gov/data/forecasts/taf/stations/{station_id}.TXT"
        text = await self._request_text(url)
        return _noaa_taf_snapshot(station_id, url, text)

    async def get_skyvector_airport(self, station: str) -> AviationSourceSnapshot:
        station_id = station.strip().upper()
        url = SKYVECTOR_URLS[station_id]
        text = await self._request_browser_text(url)
        plain = _html_to_text(text)
        title = _title(text) or f"{station_id} SkyVector"
        lines = _select_lines(
            plain,
            keywords=("Airport Communications", "Runway", "Nearby Navigation", "Coordinates", "Elevation", station_id),
            limit=6,
        )
        if not lines:
            lines = [f"{station_id} SkyVector chart/overlay link"]
        return _snapshot(
            source="SkyVector",
            station=station_id,
            kind="airport_overlay",
            title=title,
            source_url=url,
            summary_lines=lines,
            raw_text="\n".join(lines),
        )

    async def get_airnavradar_airport(self, station: str) -> AviationSourceSnapshot:
        station_id = station.strip().upper()
        url = f"https://www.airnavradar.com/data/airports/{station_id}"
        text = await self._request_browser_text(url, referer="https://www.google.com/")
        plain = _html_to_text(text)
        title = _title(text) or f"{station_id} AirNavRadar"
        lines = _select_lines(
            plain,
            keywords=("recently received metar", "recently received taf", "runway", "airport info", station_id),
            limit=5,
        )
        if not lines:
            lines = [title]
        return _snapshot(
            source="AirNavRadar",
            station=station_id,
            kind="airport_radar_metadata",
            title=title,
            source_url=url,
            summary_lines=lines,
            raw_text="\n".join(lines),
        )

    async def get_ifatc_airport(self, station: str) -> AviationSourceSnapshot:
        station_id = station.strip().upper()
        url = f"https://www.ifatc.org/airports?apt={station_id}"
        text = await self._request_ifatc_airport(station_id)
        plain = _html_to_text(text)
        lines = _select_lines(
            plain,
            keywords=(
                "METAR:",
                "Class:",
                "Elevation:",
                "Closest city:",
                "Runways",
                "Frequencies",
                "Tower",
                "Ground",
                "ATIS",
                "Gate information",
            ),
            limit=8,
        )
        if not lines:
            lines = [f"{station_id} IFATC airport page fetched"]
        return _snapshot(
            source="IFATC",
            station=station_id,
            kind="airport_runway_frequency_metadata",
            title=f"{station_id} IFATC airport info",
            source_url=url,
            summary_lines=lines,
            raw_text="\n".join(lines),
        )

    async def get_bigorre_notam(self, station: str) -> AviationSourceSnapshot:
        station_id = station.strip().upper()
        url = f"https://www.bigorre.org/aero/notam/{station_id.lower()}/en"
        text = await self._request_browser_text(url)
        plain = _html_to_text(text)
        title = _title(text) or f"{station_id} Bigorre NOTAM"
        if "no notam to our knowledge" in plain.lower():
            lines = ["No NOTAM to Bigorre knowledge; verify official source before operations."]
        else:
            lines = _select_lines(
                plain,
                keywords=("NOTAM for", "A)", "B)", "C)", "Q)", station_id),
                limit=8,
            )
            if not any("notam" in line.lower() for line in lines):
                lines.insert(0, f"NOTAM page fetched for {station_id}")
        return _snapshot(
            source="Bigorre",
            station=station_id,
            kind="notam_backup",
            title=title,
            source_url=url,
            summary_lines=lines,
            raw_text="\n".join(lines),
        )

    async def get_aviapages_airport(self, station: str) -> AviationSourceSnapshot:
        station_id = station.strip().upper()
        url = f"{self.aviapages_base_url}/airports/{station_id}/"
        headers = {"Authorization": f"Token {self.settings.aviapages_api_token}"}
        payload = await self._request_json(url, headers=headers)
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "Aviapages payload is not an object")
        lines = _aviapages_lines(payload)
        return _snapshot(
            source="Aviapages",
            station=station_id,
            kind="notam_airport_metadata",
            title=str(payload.get("name") or payload.get("icao") or f"{station_id} Aviapages"),
            source_url=url,
            summary_lines=lines,
            raw_json=payload,
            raw_text=json.dumps(payload, sort_keys=True, ensure_ascii=False)[:4000],
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            station = self.settings.aviation_source_watch_station_keys[0]
            await self.get_noaa_metar(station)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))

    async def _request_browser_text(self, url: str, *, referer: str | None = None) -> str:
        headers = dict(_BROWSER_HEADERS)
        if referer:
            headers["Referer"] = referer

        async def do_request() -> str:
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers=headers,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.text

        return await self._with_retries(do_request)

    async def _request_ifatc_airport(self, station: str) -> str:
        async def do_request() -> str:
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers=_BROWSER_HEADERS,
                follow_redirects=True,
            ) as client:
                response = await client.get("https://www.ifatc.org/airports")
                response.raise_for_status()
                csrf_token = _csrf_token(response.text)
                data = {"code_enter": station, "code_search": ""}
                if csrf_token:
                    data["csrf_token"] = csrf_token
                response = await client.post("https://www.ifatc.org/airports", data=data)
                response.raise_for_status()
                return response.text

        return await self._with_retries(do_request)

    async def _safe(self, awaitable: Any) -> AviationSourceSnapshot | None:
        try:
            return await awaitable
        except Exception as exc:
            self.logger.warning("aviation watch source fetch failed: %s", exc)
            return None


def _noaa_snapshot(station: str, url: str, text: str) -> AviationSourceSnapshot:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    observed_at = _parse_noaa_time(lines[0]) if lines else None
    metar = lines[-1] if lines else "NOAA METAR text empty"
    return _snapshot(
        source="NOAA",
        station=station,
        kind="raw_metar_fast_fallback",
        title=f"{station} NOAA raw METAR",
        source_url=url,
        summary_lines=[metar],
        observed_at=observed_at,
        raw_text=text,
    )


def _noaa_taf_snapshot(station: str, url: str, text: str) -> AviationSourceSnapshot:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    issued_at = _parse_noaa_time(lines[0]) if lines else None
    taf_lines = lines[1:] if len(lines) > 1 else []
    taf = " ".join(taf_lines) if taf_lines else "NOAA TAF text empty"
    taf = re.sub(r"\s+", " ", taf).strip()
    taf = re.sub(r"^TAF\s+TAF\s+", "TAF ", taf)
    return _snapshot(
        source="NOAA",
        station=station,
        kind="raw_taf_fast_fallback",
        title=f"{station} NOAA raw TAF",
        source_url=url,
        summary_lines=[taf],
        observed_at=issued_at,
        raw_text=text,
    )


def _snapshot(
    *,
    source: str,
    station: str,
    kind: str,
    title: str,
    source_url: str,
    summary_lines: list[str],
    observed_at: datetime | None = None,
    raw_text: str | None = None,
    raw_json: dict[str, Any] | None = None,
) -> AviationSourceSnapshot:
    normalized = "\n".join(line.strip() for line in summary_lines if line.strip()) or raw_text or title
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return AviationSourceSnapshot(
        source=source,
        station=station.strip().upper(),
        kind=kind,
        title=title.strip(),
        source_url=source_url,
        fetch_timestamp=datetime.now(timezone.utc),
        observed_at=observed_at,
        summary_lines=[line.strip() for line in summary_lines if line.strip()][:10],
        fingerprint=fingerprint,
        raw_text=raw_text,
        raw_json=raw_json or {},
    )


def _parse_noaa_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


def _parse_aw_time(report_time: Any, obs_time: Any) -> datetime | None:
    parsed = _parse_iso(report_time)
    if parsed:
        return parsed
    if obs_time is None:
        return None
    try:
        return datetime.fromtimestamp(int(obs_time), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _first_payload_object(payload: object, label: str) -> dict[str, Any]:
    if not isinstance(payload, list) or not payload:
        raise SourceError("AviationWatch", f"{label} payload is empty")
    row = payload[0]
    if not isinstance(row, dict):
        raise SourceError("AviationWatch", f"{label} payload is not an object")
    return row


def _aviationweather_metar_lines(row: dict[str, Any]) -> list[str]:
    raw = str(row.get("rawOb") or "").strip()
    lines = [raw] if raw else []
    wind = _wind_line(row.get("wdir"), row.get("wspd"), row.get("wgst"))
    parts = [
        f"temp {row.get('temp')}°C" if row.get("temp") not in (None, "") else "",
        f"dew {row.get('dewp')}°C" if row.get("dewp") not in (None, "") else "",
        wind,
        f"QNH {row.get('altim')}" if row.get("altim") not in (None, "") else "",
        f"vis {row.get('visib')}" if row.get("visib") not in (None, "") else "",
        f"flt {row.get('fltCat')}" if row.get("fltCat") not in (None, "") else "",
    ]
    status = " · ".join(part for part in parts if part)
    if status:
        lines.append(status)
    clouds = _cloud_line(row.get("clouds"))
    if clouds:
        lines.append(clouds)
    return lines or ["AviationWeather METAR JSON fetched"]


def _aviationweather_taf_lines(row: dict[str, Any]) -> list[str]:
    lines = []
    raw = str(row.get("rawTAF") or "").strip()
    if raw:
        lines.append(raw)
    for period in row.get("fcsts") or []:
        if not isinstance(period, dict):
            continue
        period_line = _taf_period_line(period)
        if period_line:
            lines.append(period_line)
        if len(lines) >= 4:
            break
    return lines or ["AviationWeather TAF JSON fetched"]


def _taf_period_line(period: dict[str, Any]) -> str:
    start = _parse_aw_time(None, period.get("timeFrom"))
    end = _parse_aw_time(None, period.get("timeTo"))
    window = f"{start:%d %H:%M}-{end:%d %H:%M} UTC" if start and end else "TAF period"
    change = period.get("fcstChange") or "BASE"
    probability = f" PROB{period.get('probability')}" if period.get("probability") not in (None, "") else ""
    wind = _wind_line(period.get("wdir"), period.get("wspd"), period.get("wgst"))
    weather = period.get("wxString") or "NSW"
    clouds = _cloud_line(period.get("clouds"))
    return " · ".join(part for part in [window, f"{change}{probability}", wind, str(weather), clouds] if part)


def _wind_line(direction: Any, speed: Any, gust: Any = None) -> str:
    if speed in (None, ""):
        return ""
    direction_text = "VRB" if direction in (None, "", "VRB") else str(direction)
    gust_text = f"G{gust}" if gust not in (None, "") else ""
    return f"wind {direction_text}/{speed}{gust_text}kt"


def _cloud_line(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts = []
    for cloud in value[:4]:
        if not isinstance(cloud, dict):
            continue
        cover = cloud.get("cover")
        base = cloud.get("base")
        cloud_type = cloud.get("type")
        item = str(cover or "")
        if base not in (None, ""):
            item = f"{item}{base}"
        if cloud_type not in (None, ""):
            item = f"{item}{cloud_type}"
        if item:
            parts.append(item)
    return f"clouds {' '.join(parts)}" if parts else ""


def _html_to_text(value: str) -> str:
    text = re.sub(r"<(script|style).*?</\1>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|tr|td|th|h\d|section|table)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()


def _title(value: str) -> str | None:
    match = re.search(r"<title>(.*?)</title>", value, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()


def _csrf_token(value: str) -> str | None:
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', value)
    return html.unescape(match.group(1)) if match else None


def _select_lines(text: str, *, keywords: tuple[str, ...], limit: int) -> list[str]:
    raw_lines = [line.strip(" •\t") for line in text.splitlines()]
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw_lines if line.strip()]
    selected: list[str] = []
    seen: set[str] = set()
    for line in lines:
        lowered = line.lower()
        if not any(keyword.lower() in lowered for keyword in keywords):
            continue
        if len(line) > 260:
            line = f"{line[:257]}..."
        if line in seen:
            continue
        seen.add(line)
        selected.append(line)
        if len(selected) >= limit:
            break
    return selected


def _aviapages_lines(payload: dict[str, Any]) -> list[str]:
    lines = []
    for key in ("name", "icao", "iata", "city", "country", "type", "timezone"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            lines.append(f"{key}: {value}")
    notams = payload.get("notams") or payload.get("notam")
    if isinstance(notams, list):
        lines.append(f"notam_count: {len(notams)}")
        for item in notams[:3]:
            if isinstance(item, dict):
                text = item.get("text") or item.get("message") or item.get("raw")
                if text:
                    lines.append(str(text)[:260])
            elif item:
                lines.append(str(item)[:260])
    elif notams:
        lines.append(f"notam: {str(notams)[:260]}")
    return lines or ["Aviapages airport metadata fetched"]
