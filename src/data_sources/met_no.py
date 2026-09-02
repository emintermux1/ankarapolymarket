from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class MetNoAdapter(HttpSource):
    source_name = "MET Norway"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.url = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        payload = await self._request_json(
            self.url,
            params={
                "lat": f"{self.settings.ltac_latitude:.4f}",
                "lon": f"{self.settings.ltac_longitude:.4f}",
                "altitude": self.settings.ltac_elevation_m,
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "Forecast payload is not an object")
        timeseries = ((payload.get("properties") or {}).get("timeseries") or [])
        points: list[ModelHourlyPoint] = []
        for item in timeseries:
            if not isinstance(item, dict):
                continue
            ts = _parse_time(item.get("time"))
            if ts is None or ts.astimezone(ZoneInfo(self.settings.report_timezone)).date() != target_date:
                continue
            details = (((item.get("data") or {}).get("instant") or {}).get("details") or {})
            next_1h = (((item.get("data") or {}).get("next_1_hours") or {}).get("details") or {})
            points.append(
                ModelHourlyPoint(
                    time=ts.astimezone(ZoneInfo(self.settings.report_timezone)),
                    temperature_2m_c=_safe_float(details.get("air_temperature")),
                    relative_humidity_pct=_safe_float(details.get("relative_humidity")),
                    precipitation_mm=_safe_float(next_1h.get("precipitation_amount")),
                    cloud_cover_pct=_safe_float(details.get("cloud_area_fraction")),
                    wind_speed_10m_kt=_ms_to_kt(_safe_float(details.get("wind_speed"))),
                    wind_direction_10m_deg=_safe_float(details.get("wind_from_direction")),
                    pressure_msl_hpa=_safe_float(details.get("air_pressure_at_sea_level")),
                )
            )
        temperatures = [point.temperature_2m_c for point in points if point.temperature_2m_c is not None]
        return ModelForecast(
            model="met_no",
            available=bool(temperatures),
            target_date=target_date,
            hourly=points,
            tmax_c=float(max(temperatures)) if temperatures else None,
            unavailable_reason=None if temperatures else "MET Norway returned no LTAC temperature points for target date",
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            await self.get_model_forecast(datetime.now(ZoneInfo(self.settings.report_timezone)).date())
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "M"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ms_to_kt(value: float | None) -> float | None:
    return round(value * 1.943844, 2) if value is not None else None
