from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import ActualResult, ModelForecast, SourceHealth, SourceState, round_market_temperature_c


class VisualCrossingAdapter(HttpSource):
    source_name = "VisualCrossing"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"

    async def get_model_forecast(self, target_date: date) -> ModelForecast:
        if not self.settings.visualcrossing_api_key:
            raise SourceError(self.source_name, "VISUALCROSSING_API_KEY not configured")
        payload = await self._fetch_payload(target_date)
        day = _target_day(payload, target_date)
        if day is None:
            return ModelForecast(
                model="visual_crossing",
                available=False,
                target_date=target_date,
                unavailable_reason="target date not present in Visual Crossing response",
            )
        tmax = _safe_float(day.get("tempmax"))
        if tmax is None:
            return ModelForecast(
                model="visual_crossing",
                available=False,
                target_date=target_date,
                unavailable_reason="tempmax missing in Visual Crossing response",
                raw_model_key_map={"tempmax": "days[].tempmax"},
            )
        return ModelForecast(
            model="visual_crossing",
            available=True,
            target_date=target_date,
            tmax_c=tmax,
            raw_model_key_map={"tempmax": "days[].tempmax"},
        )

    async def get_daily_result(self, target_date: date) -> ActualResult:
        if not self.settings.visualcrossing_api_key:
            raise SourceError(self.source_name, "VISUALCROSSING_API_KEY not configured")
        payload = await self._fetch_payload(target_date)
        day = _target_day(payload, target_date)
        tmax = _safe_float(day.get("tempmax")) if day else None
        if tmax is None:
            return ActualResult(
                target_date=target_date,
                source=self.source_name,
                fetched_at=datetime.now(timezone.utc),
                raw_payload={"resolvedAddress": payload.get("resolvedAddress")},
                unavailable_reason="Visual Crossing tempmax unavailable",
                manual_required=True,
            )
        return ActualResult(
            target_date=target_date,
            source=self.source_name,
            fetched_at=datetime.now(timezone.utc),
            tmax_c=tmax,
            rounded_tmax_c=round_market_temperature_c(tmax),
            raw_payload={
                "resolvedAddress": payload.get("resolvedAddress"),
                "timezone": payload.get("timezone"),
                "source": day.get("source"),
            },
        )

    async def health(self) -> SourceHealth:
        if not self.settings.visualcrossing_api_key:
            return SourceHealth(
                source=self.source_name,
                state=SourceState.UNAVAILABLE,
                message="VISUALCROSSING_API_KEY not configured",
            )
        started = datetime.now(timezone.utc)
        try:
            await self.get_model_forecast(datetime.now(timezone.utc).date())
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))

    async def _fetch_payload(self, target_date: date) -> dict[str, Any]:
        location = quote(
            self.settings.visualcrossing_location.strip() or f"{self.settings.ltac_latitude},{self.settings.ltac_longitude}",
            safe=",",
        )
        url = f"{self.base_url}/{location}/{target_date.isoformat()}/{target_date.isoformat()}"
        payload = await self._request_json(
            url,
            params={
                "unitGroup": "metric",
                "key": self.settings.visualcrossing_api_key,
                "options": "usefcst,usestatsfcst",
                "contentType": "json",
                "include": "days,current",
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "payload is not an object")
        return payload


def _target_day(payload: dict[str, Any], target_date: date) -> dict[str, Any] | None:
    for day in payload.get("days") or []:
        if isinstance(day, dict) and day.get("datetime") == target_date.isoformat():
            return day
    return None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)
