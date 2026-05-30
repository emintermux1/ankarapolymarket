from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import (
    METARNormalized,
    SourceHealth,
    SourceState,
    TAFForecastPeriod,
    TAFNormalized,
    relative_humidity_from_temp_dewpoint,
)


class AviationWeatherAdapter(HttpSource):
    source_name = "AviationWeather"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://aviationweather.gov/api/data"

    async def get_metar(self, station: str | None = None) -> METARNormalized:
        station_id = (station or self.settings.ltac_icao).strip().upper()
        url = f"{self.base_url}/metar"
        payload = await self._request_json(
            url,
            params={"ids": station_id, "format": "json"},
        )
        if not isinstance(payload, list) or not payload:
            raise SourceError(self.source_name, "METAR payload is empty")
        row = payload[0]
        if not isinstance(row, dict):
            raise SourceError(self.source_name, "METAR payload is not an object")

        temperature = float(row["temp"])
        dew_point = float(row["dewp"])
        observation_time = _parse_aw_time(row.get("reportTime"), row.get("obsTime"))
        visibility_m = _parse_visibility_m(row.get("visib"), row.get("rawOb", ""))
        wind_direction = row.get("wdir")
        if isinstance(wind_direction, str) and not wind_direction.isdigit():
            wind_direction = None

        return METARNormalized(
            fetch_timestamp=datetime.now(timezone.utc),
            observation_time=observation_time,
            station=str(row.get("icaoId") or station_id).upper(),
            temperature_c=temperature,
            dew_point_c=dew_point,
            relative_humidity=relative_humidity_from_temp_dewpoint(temperature, dew_point),
            wind_direction_deg=int(wind_direction) if wind_direction is not None else None,
            wind_speed_kt=float(row.get("wspd") or 0.0),
            wind_gust_kt=float(row["wgst"]) if row.get("wgst") is not None else None,
            pressure_hpa=float(row["altim"]) if row.get("altim") is not None else None,
            visibility_m=visibility_m,
            cloud_layers=list(row.get("clouds") or []),
            raw_text=str(row.get("rawOb") or ""),
            raw_json=row,
        )

    async def get_taf(self, station: str | None = None) -> TAFNormalized:
        station_id = (station or self.settings.ltac_icao).strip().upper()
        url = f"{self.base_url}/taf"
        payload = await self._request_json(
            url,
            params={"ids": station_id, "format": "json"},
        )
        if not isinstance(payload, list) or not payload:
            raise SourceError(self.source_name, "TAF payload is empty")
        row = payload[0]
        if not isinstance(row, dict):
            raise SourceError(self.source_name, "TAF payload is not an object")

        periods = []
        for item in row.get("fcsts") or []:
            if not isinstance(item, dict):
                continue
            periods.append(
                TAFForecastPeriod(
                    time_from=_epoch_to_utc(item.get("timeFrom")),
                    time_to=_epoch_to_utc(item.get("timeTo")),
                    change=item.get("fcstChange"),
                    probability=item.get("probability"),
                    wind_direction_deg=_safe_int(item.get("wdir")),
                    wind_speed_kt=_safe_float(item.get("wspd")),
                    wind_gust_kt=_safe_float(item.get("wgst")),
                    visibility_m=_parse_visibility_m(item.get("visib"), ""),
                    weather=item.get("wxString"),
                    clouds=list(item.get("clouds") or []),
                )
            )

        return TAFNormalized(
            fetch_timestamp=datetime.now(timezone.utc),
            issue_time=_parse_iso(row.get("issueTime")) or _parse_iso(row.get("bulletinTime")) or datetime.now(timezone.utc),
            valid_from=_epoch_to_utc(row.get("validTimeFrom")),
            valid_to=_epoch_to_utc(row.get("validTimeTo")),
            station=str(row.get("icaoId") or station_id).upper(),
            raw_text=str(row.get("rawTAF") or ""),
            periods=periods,
            raw_json=row,
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            await self.get_metar()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _parse_aw_time(report_time: Any, obs_time: Any) -> datetime:
    parsed = _parse_iso(report_time)
    if parsed:
        return parsed
    return _epoch_to_utc(obs_time)


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


def _epoch_to_utc(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.isdigit():
        return None
    return int(value)


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _parse_visibility_m(value: Any, raw_text: str) -> int | None:
    if isinstance(raw_text, str) and "9999" in raw_text:
        return 9999
    if value in (None, ""):
        return None
    text = str(value)
    if text.endswith("+"):
        return 9999
    try:
        miles = float(text)
        return int(round(miles * 1609.344))
    except ValueError:
        return None
