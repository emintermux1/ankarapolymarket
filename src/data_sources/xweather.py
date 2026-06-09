from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import ModelForecast, ModelHourlyPoint, SourceHealth, SourceState


class XWeatherAdapter(HttpSource):
    """AerisWeather / xWeather adapter (requires client_id and secret)."""

    source_name = "xWeather"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def get_model_forecast(self, target_date: date) -> ModelForecast | None:
        if not self.settings.xweather_client_id or not self.settings.xweather_client_secret:
            return None
        try:
            loc = f"{self.settings.ltac_latitude},{self.settings.ltac_longitude}"
            payload = await self._request_json(
                f"https://api.aerisapi.com/forecasts/{loc}",
                params={
                    "client_id": self.settings.xweather_client_id,
                    "client_secret": self.settings.xweather_client_secret,
                    "from": target_date.isoformat(),
                    "to": target_date.isoformat(),
                },
            )
            if not isinstance(payload, dict):
                return None
            response = payload.get("response")
            if not isinstance(response, list) or not response:
                return None
            periods = response[0].get("periods")
            if not isinstance(periods, list):
                return None
            points = []
            for period in periods:
                if not isinstance(period, dict):
                    continue
                ts = _parse_time(period.get("dateTimeISO") or period.get("timestamp"))
                if ts is None:
                    continue
                temp = _safe_float(period.get("maxTempC") or period.get("tempC"))
                if temp is None:
                    continue
                points.append(ModelHourlyPoint(time=ts, temperature_2m_c=temp))
            if not points:
                return None
            temps = [p.temperature_2m_c for p in points if p.temperature_2m_c is not None]
            return ModelForecast(
                model="xweather",
                available=True,
                target_date=target_date,
                hourly=points,
                tmax_c=max(temps) if temps else None,
            )
        except Exception:
            return None

    async def health(self) -> SourceHealth:
        if not self.settings.xweather_client_id:
            return SourceHealth(source=self.source_name, state=SourceState.UNAVAILABLE, message="XWEATHER_CLIENT_ID not configured")
        started = datetime.now(timezone.utc)
        try:
            result = await self.get_model_forecast(date.today())
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if result is not None:
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message="xWeather forecast returned no data")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
