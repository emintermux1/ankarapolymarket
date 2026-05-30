from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import (
    METARNormalized,
    SourceHealth,
    SourceState,
    TAFForecastPeriod,
    TAFNormalized,
)


class CheckWXAdapter(HttpSource):
    source_name = "CheckWX"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.checkwx.com"

    async def get_metar(self, station: str | None = None) -> METARNormalized:
        station_id = (station or self.settings.ltac_icao).strip().upper()
        row = await self._decoded("metar", station_id)
        wind = row.get("wind") or {}
        barometer = row.get("barometer") or {}
        visibility = row.get("visibility") or {}
        return METARNormalized(
            source=self.source_name,
            station=str(row.get("icao") or station_id).upper(),
            fetch_timestamp=datetime.now(timezone.utc),
            observation_time=_parse_iso(row.get("observed")) or datetime.now(timezone.utc),
            temperature_c=float((row.get("temperature") or {}).get("celsius")),
            dew_point_c=float((row.get("dewpoint") or {}).get("celsius")),
            relative_humidity=_safe_int(row.get("humidity")),
            wind_direction_deg=_safe_int(wind.get("degrees")),
            wind_speed_kt=float(wind.get("speed_kts") or 0.0),
            wind_gust_kt=_safe_float(wind.get("gust_kts")),
            pressure_hpa=_safe_float(barometer.get("hpa") or barometer.get("mb")),
            visibility_m=_safe_int(visibility.get("meters")),
            cloud_layers=_cloud_layers(row.get("clouds")),
            raw_text=str(row.get("raw_text") or ""),
            raw_json=row,
        )

    async def get_taf(self, station: str | None = None) -> TAFNormalized:
        station_id = (station or self.settings.ltac_icao).strip().upper()
        row = await self._decoded("taf", station_id)
        periods = []
        for item in row.get("forecast") or []:
            if not isinstance(item, dict):
                continue
            change = item.get("change") or {}
            period = change.get("period") or {}
            wind = item.get("wind") or {}
            visibility = item.get("visibility") or {}
            periods.append(
                TAFForecastPeriod(
                    time_from=_parse_iso(period.get("from")) or datetime.now(timezone.utc),
                    time_to=_parse_iso(period.get("to")) or datetime.now(timezone.utc),
                    change=change.get("code"),
                    wind_direction_deg=_safe_int(wind.get("degrees")),
                    wind_speed_kt=_safe_float(wind.get("speed_kts")),
                    wind_gust_kt=_safe_float(wind.get("gust_kts")),
                    visibility_m=_safe_int(visibility.get("meters")),
                    weather=_conditions_text(item.get("conditions")),
                    clouds=_cloud_layers(item.get("clouds")),
                )
            )
        base_period = row.get("period") or {}
        return TAFNormalized(
            source=self.source_name,
            station=str(row.get("icao") or station_id).upper(),
            fetch_timestamp=datetime.now(timezone.utc),
            issue_time=_parse_iso(row.get("issued")) or datetime.now(timezone.utc),
            valid_from=_parse_iso(base_period.get("from")) or datetime.now(timezone.utc),
            valid_to=_parse_iso(base_period.get("to")) or datetime.now(timezone.utc),
            raw_text=str(row.get("raw_text") or ""),
            periods=periods,
            raw_json=row,
        )

    async def health(self) -> SourceHealth:
        if not self.settings.checkwx_api_key:
            return SourceHealth(source=self.source_name, state=SourceState.UNAVAILABLE, message="CHECKWX_API_KEY not configured")
        started = datetime.now(timezone.utc)
        try:
            await self.get_metar()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))

    async def _decoded(self, report_type: str, station: str | None = None) -> dict[str, Any]:
        if not self.settings.checkwx_api_key:
            raise SourceError(self.source_name, "CHECKWX_API_KEY not configured")
        station_id = (station or self.settings.ltac_icao).strip().upper()

        async def do_request() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=self.settings.http_timeout_seconds, follow_redirects=True) as client:
                response = await client.get(
                    f"{self.base_url}/{report_type}/{station_id}/decoded",
                    headers={"X-API-Key": self.settings.checkwx_api_key},
                )
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict) or not payload.get("data"):
                raise SourceError(self.source_name, f"{report_type.upper()} payload is empty")
            row = payload["data"][0]
            if not isinstance(row, dict):
                raise SourceError(self.source_name, f"{report_type.upper()} payload row is not an object")
            return row

        return await self._with_retries(do_request)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _cloud_layers(value: Any) -> list[dict[str, Any]]:
    layers = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        layers.append(
            {
                "cover": item.get("code"),
                "base": item.get("feet"),
                "type": (item.get("type") or {}).get("code") if isinstance(item.get("type"), dict) else None,
            }
        )
    return layers


def _conditions_text(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, list):
        return " ".join(str(item.get("code") or item.get("text") or "") for item in value if isinstance(item, dict)).strip() or None
    return str(value)
