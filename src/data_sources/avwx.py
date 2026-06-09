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


class AVWXAdapter(HttpSource):
    """AVWX REST API — full decoded METAR/TAF for LTAC/LTFM.

    AVWX provides structured aviation weather with runway visual range,
    flight categories, wind shear alerts, and more granular cloud data."""

    source_name = "AVWX"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://avwx.rest/api"

    def _headers(self) -> dict[str, str]:
        if not self.settings.avwx_api_key:
            raise SourceError(self.source_name, "AVWX_API_KEY not configured")
        return {"Authorization": f"Bearer {self.settings.avwx_api_key}"}

    async def get_metar(self, station: str | None = None, *, raw: bool = False) -> METARNormalized:
        station_id = (station or self.settings.ltac_icao).strip().upper()
        params: dict[str, Any] = {"onfail": "cache"} if not raw else {}
        payload = await self._request_json(
            f"{self.base_url}/metar/{station_id}?options=info,translate",
            headers=self._headers(),
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "AVWX METAR payload is not an object")

        temp = payload.get("temperature")
        dew = payload.get("dewpoint")
        wind = payload.get("wind_direction") or {}
        wind_speed = payload.get("wind_speed") or {}
        wind_gust = payload.get("wind_gust") or {}
        altimeter = payload.get("altimeter") or {}
        visibility = payload.get("visibility") or {}
        clouds = payload.get("clouds") or []

        temp_c = float(temp.get("value")) if isinstance(temp, dict) and temp.get("value") is not None else None
        dew_c = float(dew.get("value")) if isinstance(dew, dict) and dew.get("value") is not None else None

        return METARNormalized(
            source=self.source_name,
            station=station_id,
            fetch_timestamp=datetime.now(timezone.utc),
            observation_time=_parse_avwx_time(payload.get("time", {}).get("dt")),
            temperature_c=temp_c or 0.0,
            dew_point_c=dew_c or 0.0,
            relative_humidity=(
                relative_humidity_from_temp_dewpoint(temp_c, dew_c)
                if temp_c is not None and dew_c is not None
                else None
            ),
            wind_direction_deg=_safe_int(wind.get("value")) if isinstance(wind, dict) else None,
            wind_speed_kt=float(wind_speed.get("value") or 0.0) if isinstance(wind_speed, dict) else 0.0,
            wind_gust_kt=_safe_float(wind_gust.get("value")) if isinstance(wind_gust, dict) else None,
            pressure_hpa=_safe_float(altimeter.get("value")) if isinstance(altimeter, dict) else None,
            visibility_m=_safe_int(visibility.get("value")) if isinstance(visibility, dict) else None,
            cloud_layers=_avwx_clouds(clouds),
            raw_text=str(payload.get("raw") or ""),
            raw_json=payload,
        )

    async def get_taf(self, station: str | None = None) -> TAFNormalized:
        station_id = (station or self.settings.ltac_icao).strip().upper()
        payload = await self._request_json(
            f"{self.base_url}/taf/{station_id}?options=info,translate",
            headers=self._headers(),
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "AVWX TAF payload is not an object")

        periods = []
        for forecast in payload.get("forecast") or []:
            if not isinstance(forecast, dict):
                continue
            wind = forecast.get("wind_direction") or {}
            wind_speed = forecast.get("wind_speed") or {}
            wind_gust = forecast.get("wind_gust") or {}
            vis = forecast.get("visibility") or {}
            periods.append(
                TAFForecastPeriod(
                    time_from=_parse_avwx_time(forecast.get("start_time", {}).get("dt")),
                    time_to=_parse_avwx_time(forecast.get("end_time", {}).get("dt")),
                    change=forecast.get("type"),
                    probability=forecast.get("probability", {}).get("value") if isinstance(forecast.get("probability"), dict) else None,
                    wind_direction_deg=_safe_int(wind.get("value")) if isinstance(wind, dict) else None,
                    wind_speed_kt=_safe_float(wind_speed.get("value")) if isinstance(wind_speed, dict) else None,
                    wind_gust_kt=_safe_float(wind_gust.get("value")) if isinstance(wind_gust, dict) else None,
                    visibility_m=_safe_int(vis.get("value")) if isinstance(vis, dict) else None,
                    weather=forecast.get("wx_codes"),
                    clouds=_avwx_clouds(forecast.get("clouds") or []),
                )
            )

        time_info = payload.get("time") or {}
        return TAFNormalized(
            source=self.source_name,
            station=station_id,
            fetch_timestamp=datetime.now(timezone.utc),
            issue_time=_parse_avwx_time(time_info.get("dt")),
            valid_from=_parse_avwx_time(payload.get("start_time", {}).get("dt")),
            valid_to=_parse_avwx_time(payload.get("end_time", {}).get("dt")),
            raw_text=str(payload.get("raw") or ""),
            periods=periods,
            raw_json=payload,
        )

    async def health(self) -> SourceHealth:
        if not self.settings.avwx_api_key:
            return SourceHealth(source=self.source_name, state=SourceState.UNAVAILABLE, message="AVWX_API_KEY not configured")
        started = datetime.now(timezone.utc)
        try:
            await self.get_metar()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _parse_avwx_time(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _avwx_clouds(clouds: list[dict]) -> list[dict[str, Any]]:
    result = []
    for cloud in clouds:
        if not isinstance(cloud, dict):
            continue
        result.append({
            "cover": cloud.get("type"),
            "base": _safe_int(cloud.get("altitude")),
            "type": cloud.get("modifier"),
        })
    return result
