from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import SourceHealth, SourceState


class OpenSkyAdapter(HttpSource):
    """OpenSky Network — live flight tracking near LTAC/LTFM.

    Free public API for aircraft state vectors. Tracks arrivals/departures
    within bounding boxes around Ankara Esenboğa and İstanbul airports."""

    source_name = "OpenSky"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.base_url = "https://opensky-network.org/api"

    async def get_flights_near_ltac(self, radius_km: float = 50.0) -> list[dict[str, Any]]:
        """Return flights near LTAC Esenboğa within radius_km."""
        lat = self.settings.ltac_latitude
        lon = self.settings.ltac_longitude
        delta = radius_km / 111.0
        return await self._fetch_box(lat - delta, lon - delta, lat + delta, lon + delta)

    async def get_flights_near_ltfm(self, radius_km: float = 50.0) -> list[dict[str, Any]]:
        """Return flights near LTFM İstanbul Airport within radius_km."""
        lat, lon = 41.2608, 28.7419
        delta = radius_km / 111.0
        return await self._fetch_box(lat - delta, lon - delta, lat + delta, lon + delta)

    async def get_flights_ankara_istanbul_corridor(self) -> list[dict[str, Any]]:
        """Return flights in the Ankara-İstanbul air corridor."""
        return await self._fetch_box(39.5, 28.0, 41.5, 33.5)

    async def _fetch_box(
        self, lamin: float, lomin: float, lamax: float, lomax: float
    ) -> list[dict[str, Any]]:
        payload = await self._request_json(
            f"{self.base_url}/states/all",
            params={
                "lamin": lamin,
                "lomin": lomin,
                "lamax": lamax,
                "lomax": lomax,
            },
        )
        if not isinstance(payload, dict):
            return []
        states = payload.get("states") or []
        if not isinstance(states, list):
            return []
        flights = []
        for state in states[:30]:
            if not isinstance(state, list) or len(state) < 8:
                continue
            flights.append({
                "icao24": state[0],
                "callsign": (state[1] or "").strip(),
                "origin_country": state[2],
                "longitude": state[5],
                "latitude": state[6],
                "baro_altitude_m": state[7],
                "velocity_m_s": state[9],
                "heading_deg": state[10],
                "on_ground": state[8],
            })
        return flights

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            flights = await self.get_flights_near_ltac(radius_km=100.0)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(
                source=self.source_name,
                state=SourceState.OK,
                latency_ms=latency,
                message=f"{len(flights)} flights tracked near LTAC",
            )
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))
