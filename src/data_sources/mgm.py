from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import (
    MGMStationObservation,
    SourceHealth,
    SourceState,
)


class MGMAdapter(HttpSource):
    source_name = "MGM"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://servis.mgm.gov.tr/web"
        self.station_id = "17130"
        self.station_name = "Ankara/Esenboğa"

    async def get_current_observation(self) -> dict[str, Any]:
        """Fetch current observation from MGM for Ankara station 17130.

        Returns a dict with temp_c, humidity, wind_speed_kt, wind_dir, pressure.
        Returns empty dict on failure.
        """
        url = f"{self.base_url}/sondurumlar"
        try:
            payload = await self._request_json(
                url,
                params={"merkezid": self.station_id},
            )
        except SourceError:
            try:
                return await self._parse_observation_html()
            except SourceError:
                return {}
        if not isinstance(payload, list) or not payload:
            return {}
        row = payload[0]
        if not isinstance(row, dict):
            return {}
        result: dict[str, Any] = {}
        result["temp_c"] = _safe_float(row.get("sicaklik"))
        result["humidity"] = _safe_int(row.get("nem"))
        result["wind_speed_kt"] = _kmh_to_kt(row.get("ruzgarHiz"))
        result["wind_dir"] = _safe_int(row.get("ruzgarYon"))
        result["pressure"] = _safe_float(row.get("denizSeviyesiBasinc"))
        return result

    async def get_daily_forecast(self, target_date: date) -> dict[str, Any]:
        """Fetch daily forecast from MGM for Ankara station 17130.

        Returns a dict with tmax_c, tmin_c.
        """
        url = f"{self.base_url}/tahminler/gunluk"
        try:
            payload = await self._request_json(
                url,
                params={"merkezid": self.station_id},
            )
        except SourceError:
            self.logger.warning("MGM daily forecast JSON failed, trying HTML")
            return {}
        if not isinstance(payload, list) or not payload:
            return {}
        target_str = target_date.strftime("%d.%m.%Y")
        for row in payload:
            if not isinstance(row, dict):
                continue
            if row.get("tarih") == target_str:
                return {
                    "tmax_c": _safe_float(row.get("makSicaklik")),
                    "tmin_c": _safe_float(row.get("minSicaklik")),
                }
        return {}

    async def get_observation_snapshot(self) -> MGMStationObservation | None:
        """Return a fully typed MGMStationObservation for current conditions."""
        obs = await self.get_current_observation()
        if not obs:
            return None
        tz = ZoneInfo(self.settings.report_timezone)
        return MGMStationObservation(
            fetch_timestamp=datetime.now(timezone.utc),
            station_id=self.station_id,
            station_name=self.station_name,
            observation_time=datetime.now(tz),
            temperature_c=obs.get("temp_c"),
            relative_humidity=obs.get("humidity"),
            wind_direction_deg=obs.get("wind_dir"),
            wind_speed_kt=obs.get("wind_speed_kt"),
            pressure_hpa=obs.get("pressure"),
            raw_json=obs,
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            obs = await self.get_current_observation()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if obs:
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(
                source=self.source_name,
                state=SourceState.DEGRADED,
                latency_ms=latency,
                message="MGM returned empty observation data",
            )
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))

    async def _parse_observation_html(self) -> dict[str, Any]:
        """Fallback: scrape MGM sondurumlar page HTML."""
        url = f"{self.base_url}/sondurumlar?merkezid={self.station_id}"
        text = await self._request_text(url)
        result: dict[str, Any] = {}
        temp_match = re.search(r"Sıcaklık[:\s]*([\-\d.]+)\s*°C", text)
        if temp_match:
            result["temp_c"] = _safe_float(temp_match.group(1))
        humidity_match = re.search(r"Nem[:\s]*%?(\d+)", text)
        if humidity_match:
            result["humidity"] = int(humidity_match.group(1))
        wind_match = re.search(r"Rüzgar[:\s]*(\d+)\s*°?\s*/\s*(\d+(?:\.\d+)?)\s*km/s", text)
        if wind_match:
            result["wind_dir"] = int(wind_match.group(1))
            result["wind_speed_kt"] = _kmh_to_kt(wind_match.group(2))
        pressure_match = re.search(r"Basınç[:\s]*([\d.]+)\s*hPa", text)
        if pressure_match:
            result["pressure"] = _safe_float(pressure_match.group(1))
        return result


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    parsed = _safe_float(value)
    return int(round(parsed)) if parsed is not None else None


def _kmh_to_kt(value: Any) -> float | None:
    raw = _safe_float(value)
    if raw is None:
        return None
    return raw * 0.539957
