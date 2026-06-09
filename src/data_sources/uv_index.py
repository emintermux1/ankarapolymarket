from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import SourceHealth, SourceState, UVSnapshot


class UVIndexAdapter(HttpSource):
    source_name = "UV-Index"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://api.open-meteo.com/v1/forecast"

    async def get_current_uv(self) -> dict[str, Any]:
        """Fetch current UV index from Open-Meteo for LTAC coordinates."""
        payload = await self._request_json(
            self.base_url,
            params={
                "latitude": self.settings.ltac_latitude,
                "longitude": self.settings.ltac_longitude,
                "daily": "uv_index_max",
                "timezone": self.settings.report_timezone,
                "forecast_days": 1,
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "UV payload is not an object")
        daily = payload.get("daily") or {}
        if not isinstance(daily, dict):
            raise SourceError(self.source_name, "UV daily data is not an object")
        uv_values = daily.get("uv_index_max") or []
        times = daily.get("time") or []
        uv_index = None
        fetch_date = date.today().isoformat()
        for idx, uv_val in enumerate(uv_values):
            if uv_val is not None:
                uv_index = float(uv_val)
                if idx < len(times):
                    fetch_date = str(times[idx])
                break

        return {
            "uv_index_max": uv_index,
            "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
            "target_date": fetch_date,
        }

    async def get_uv_forecast(self, target_date: date) -> dict[str, Any]:
        """Fetch UV forecast for a specific target date."""
        days_diff = (target_date - date.today()).days
        forecast_days = max(days_diff + 2, 1)
        payload = await self._request_json(
            self.base_url,
            params={
                "latitude": self.settings.ltac_latitude,
                "longitude": self.settings.ltac_longitude,
                "daily": "uv_index_max",
                "timezone": self.settings.report_timezone,
                "forecast_days": forecast_days,
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "UV forecast payload is not an object")
        daily = payload.get("daily") or {}
        if not isinstance(daily, dict):
            raise SourceError(self.source_name, "UV forecast daily data is not an object")
        uv_values = daily.get("uv_index_max") or []
        times = daily.get("time") or []
        target_str = target_date.isoformat()
        for idx, ts in enumerate(times):
            if str(ts) == target_str and idx < len(uv_values) and uv_values[idx] is not None:
                return {
                    "uv_index_max": float(uv_values[idx]),
                    "target_date": target_str,
                }
        return {"uv_index_max": None, "target_date": target_date.isoformat()}

    async def get_uv_snapshot(self, target_date: date | None = None) -> UVSnapshot | None:
        """Return a fully typed UVSnapshot."""
        if target_date:
            data = await self.get_uv_forecast(target_date)
        else:
            data = await self.get_current_uv()
        uv_index = data.get("uv_index_max")
        if uv_index is None:
            return None
        return UVSnapshot(
            fetch_timestamp=datetime.now(timezone.utc),
            uv_index_max=uv_index,
            target_date=date.fromisoformat(data.get("target_date", date.today().isoformat())),
            raw_json=data,
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            await self.get_current_uv()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))
