from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import AviationSourceSnapshot, SourceHealth, SourceState


_NOAA_SIGWX_URL = "https://aviationweather.gov/sigwx"
_NOAA_GFA_URL = "https://aviationweather.gov/gfa"
_NOAA_WINDS_URL = "https://aviationweather.gov/windtemp"
_NOAA_PIREP_URL = "https://aviationweather.gov/api/data/pirep"


class NOAAAviationAdapter(HttpSource):
    """NOAA ADDS aviation weather products — turbulence, icing, CB, PIREPs.

    Provides SIGWX charts, GFA tool links, wind/temperature aloft data,
    and pilot reports for LTAC/LTFM corridor enrichment."""

    source_name = "NOAA-Aviation"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def get_pireps_near_ltac(self, radius_km: float = 200.0) -> list[dict[str, Any]]:
        """Fetch PIREPs near LTAC."""
        return await self._fetch_pireps(
            self.settings.ltac_latitude, self.settings.ltac_longitude, radius_km
        )

    async def get_pireps_near_ltfm(self, radius_km: float = 200.0) -> list[dict[str, Any]]:
        """Fetch PIREPs near LTFM İstanbul."""
        return await self._fetch_pireps(41.2608, 28.7419, radius_km)

    async def _fetch_pireps(self, lat: float, lon: float, radius_km: float) -> list[dict[str, Any]]:
        try:
            payload = await self._request_json(
                _NOAA_PIREP_URL,
                params={"format": "json", "area": f"{radius_km / 1852:.0f}nm"},
            )
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        pireps = []
        for row in payload[:15]:
            if not isinstance(row, dict):
                continue
            pirep_lat = _safe_float(row.get("lat"))
            pirep_lon = _safe_float(row.get("lon"))
            if pirep_lat is not None and pirep_lon is not None:
                dist = _haversine(lat, lon, pirep_lat, pirep_lon)
                if dist > radius_km:
                    continue
            pireps.append({
                "report_type": row.get("reportType", "PIREP"),
                "aircraft_ref": row.get("aircraftRef"),
                "altitude_ft": row.get("altitudeFt"),
                "latitude": pirep_lat,
                "longitude": pirep_lon,
                "turbulence": row.get("turbulence"),
                "icing": row.get("icing"),
                "sky_cover": row.get("skyCover"),
                "visibility": row.get("visibility"),
                "temperature_c": _safe_float(row.get("temp")),
                "wind_dir_deg": _safe_float(row.get("windDir")),
                "wind_speed_kt": _safe_float(row.get("windSpd")),
                "raw_text": str(row.get("rawText") or "")[:300],
                "distance_km": round(dist, 1) if pirep_lat and pirep_lon else None,
            })
        return sorted(pireps, key=lambda x: x.get("distance_km") or 9999)[:10]

    def sigwx_url(self) -> str:
        return _NOAA_SIGWX_URL

    def gfa_url(self) -> str:
        return _NOAA_GFA_URL

    def wind_temp_url(self) -> str:
        return _NOAA_WINDS_URL

    async def get_turkey_sigwx_snapshot(self) -> AviationSourceSnapshot:
        return AviationSourceSnapshot(
            source=self.source_name,
            station="LTAC",
            kind="sigwx_chart_link",
            title="NOAA SIGWX significant weather chart",
            source_url=_NOAA_SIGWX_URL,
            fetch_timestamp=datetime.now(timezone.utc),
            summary_lines=[
                "SIGWX charts show CB, turbulence, icing, jet streams over Turkey/Europe",
                f"Interactive: {_NOAA_GFA_URL}",
                f"Wind & temperature aloft: {_NOAA_WINDS_URL}",
            ],
            fingerprint=f"sigwx-{datetime.now(timezone.utc):%Y%m}",
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            pireps = await self.get_pireps_near_ltac(radius_km=300)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(
                source=self.source_name,
                state=SourceState.OK,
                latency_ms=latency,
                message=f"{len(pireps)} PIREPs near LTAC",
            )
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
