from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import SourceHealth, SourceState


class CopernicusAdapter(HttpSource):
    source_name = "Copernicus-CDS"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.api_url = "https://cds.climate.copernicus.eu/api/v2"

    def _auth_headers(self) -> dict[str, str]:
        if not self.settings.copernicus_cds_api_key:
            raise SourceError(self.source_name, "COPERNICUS_CDS_API_KEY not configured")
        return {"Authorization": f"Bearer {self.settings.copernicus_cds_api_key}"}

    async def get_era5_tmax(self, target_date: date) -> float | None:
        """Retrieve ERA5 tmax for LTAC coordinates on target_date.

        Submits a retrieval request, polls for completion, downloads result.
        Returns tmax_c (Kelvin converted) or None on failure.
        """
        try:
            data = await self.get_era5_data(target_date, target_date)
            if data and data[0].get("tmax_c") is not None:
                return float(data[0]["tmax_c"])
        except Exception as exc:
            self.logger.warning("ERA5 tmax fetch failed for %s: %s", target_date, exc)
        return None

    async def get_era5_data(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """Retrieve ERA5 daily tmax/tmin for date range.

        Uses the CDS API retrieval flow: submit → poll → download.
        Falls back to a simple approach for datasets available via direct access.
        """
        if not self.settings.copernicus_cds_api_key:
            raise SourceError(self.source_name, "COPERNICUS_CDS_API_KEY not configured")

        lat = self.settings.ltac_latitude
        lon = self.settings.ltac_longitude
        year = str(start_date.year)
        month = f"{start_date.month:02d}"
        day = f"{start_date.day:02d}"

        url = (
            f"{self.api_url}/resources/reanalysis-era5-single-levels?"
            f"variable=2m_temperature&year={year}&month={month}&day={day}"
            f"&time=12:00&area={lat + 0.5}/{lon - 0.5}/{lat - 0.5}/{lon + 0.5}"
            f"&format=json"
        )
        try:
            payload = await self._request_json(url, headers=self._auth_headers())
        except SourceError:
            return await self._retrieve_via_submit(start_date, end_date)

        return self._parse_era5_response(payload, start_date, end_date)

    async def _retrieve_via_submit(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        """Submit a CDS retrieval request and poll for results."""
        lat = self.settings.ltac_latitude
        lon = self.settings.ltac_longitude
        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current.isoformat())
            day = current.day + 1
            month = current.month
            year = current.year
            try:
                from datetime import timedelta
                current = (current + timedelta(days=1))
            except OverflowError:
                break

        years = list(sorted({str(d.year) for d in dates}))
        months = list(sorted({f"{d.month:02d}" for d in dates}))
        days = list(sorted({f"{d.day:02d}" for d in dates}))

        request_body = {
            "variable": "2m_temperature",
            "product_type": "reanalysis",
            "year": years,
            "month": months,
            "day": days,
            "time": ["12:00"],
            "area": [lat + 0.5, lon - 0.5, lat - 0.5, lon + 0.5],
            "format": "json",
        }
        import httpx
        async with httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds,
        ) as client:
            headers = {
                "User-Agent": "ankara-ltac-weather-bot/0.1",
            }
            if self.settings.copernicus_cds_api_key:
                headers["Authorization"] = f"Bearer {self.settings.copernicus_cds_api_key}"
            try:
                resp = await client.post(
                    f"{self.api_url}/retrieve",
                    json=request_body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                self.logger.warning("CDS retrieve submission failed: %s", exc)
                return []

        return self._parse_era5_response(data, start_date, end_date)

    def _parse_era5_response(
        self, payload: Any, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            values = payload.get("data") or payload.get("values") or []
        elif isinstance(payload, list):
            values = payload
        else:
            return []

        temps_c = []
        for item in values:
            if not isinstance(item, (int, float)):
                continue
            temp_c = float(item) - 273.15
            temps_c.append(temp_c)

        if not temps_c:
            return []

        return [{
            "tmax_c": round(max(temps_c), 2),
            "tmin_c": round(min(temps_c), 2),
            "date": start_date.isoformat(),
            "source": "ERA5",
        }]

    async def health(self) -> SourceHealth:
        if not self.settings.copernicus_cds_api_key:
            return SourceHealth(
                source=self.source_name,
                state=SourceState.UNAVAILABLE,
                message="COPERNICUS_CDS_API_KEY not configured",
            )
        started = datetime.now(timezone.utc)
        try:
            data = await self.get_era5_data(date.today(), date.today())
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if data:
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(
                source=self.source_name,
                state=SourceState.DEGRADED,
                latency_ms=latency,
                message="ERA5 returned empty data",
            )
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))
