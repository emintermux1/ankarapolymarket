from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import SourceHealth, SourceState


class MGMAdapter(HttpSource):
    source_name = "MGM"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def get_ankara_observation(self) -> dict[str, Any]:
        """Get current observation for Ankara from MGM."""
        url = f"{self.settings.mgm_observation_url}?istasyon={self.settings.mgm_station_id}&il=Ankara"
        payload = await self._request_json(url)
        if isinstance(payload, list) and payload:
            return payload[0] if isinstance(payload[0], dict) else {}
        if isinstance(payload, dict):
            return payload
        return {}

    async def get_temperature(self) -> float | None:
        """Get current Ankara temperature from MGM."""
        obs = await self.get_ankara_observation()
        for key in ("sicaklik", "sıcaklık", "temperature", "temp"):
            value = obs.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    async def get_humidity(self) -> float | None:
        """Get current Ankara humidity from MGM."""
        obs = await self.get_ankara_observation()
        for key in ("nem", "humidity", "rh"):
            value = obs.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    async def get_wind(self) -> dict[str, Any]:
        """Get current wind info from MGM."""
        obs = await self.get_ankara_observation()
        return {
            "speed_kt": _safe_float(obs.get("ruzgar_hiz") or obs.get("rüzgar_hız") or obs.get("wind_speed")),
            "direction_deg": _safe_float(obs.get("ruzgar_yon") or obs.get("rüzgar_yön") or obs.get("wind_direction")),
            "gust_kt": _safe_float(obs.get("ruzgar_hamle") or obs.get("rüzgar_hamle") or obs.get("wind_gust")),
        }

    async def get_pressure(self) -> float | None:
        """Get current Ankara pressure from MGM."""
        obs = await self.get_ankara_observation()
        for key in ("basinc", "basınç", "pressure", "basinc_hpa", "deniz_seviyesi_basinc"):
            value = obs.get(key)
            if value is not None:
                return _safe_float(value)
        return None

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            await self.get_ankara_observation()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
