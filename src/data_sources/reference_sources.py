from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import SourceHealth, SourceState


class EUMETSATAdapter(HttpSource):
    source_name = "EUMETSAT MSG Cloud Mask"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.url = "https://api.eumetsat.int/data/browse/collections/EO%3AEUM%3ADAT%3AMSG%3ACLM?format=json"

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            await self._request_text(self.url)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency, message="Cloud Mask catalogue reachable")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


class DWDIconAdapter(HttpSource):
    source_name = "DWD ICON Open Data"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.url = "https://opendata.dwd.de/weather/nwp/icon-eu/grib/00/t_2m/"

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            text = await self._request_text(self.url)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            state = SourceState.OK if "T_2M" in text.upper() else SourceState.DEGRADED
            return SourceHealth(source=self.source_name, state=state, latency_ms=latency, message="raw GRIB catalogue reachable")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


class NASAPowerAdapter(HttpSource):
    source_name = "NASA POWER"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.url = "https://power.larc.nasa.gov/api/temporal/hourly/point"

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            target = (datetime.now(timezone.utc) - timedelta(days=10)).date()
            payload = await self._request_json(
                self.url,
                params={
                    "parameters": "T2M,ALLSKY_SFC_SW_DWN,RH2M,WS10M",
                    "community": "SB",
                    "longitude": self.settings.ltac_longitude,
                    "latitude": self.settings.ltac_latitude,
                    "start": target.strftime("%Y%m%d"),
                    "end": target.strftime("%Y%m%d"),
                    "format": "JSON",
                },
            )
            values = (((payload or {}).get("properties") or {}).get("parameter") or {}) if isinstance(payload, dict) else {}
            flat_values = [value for series in values.values() if isinstance(series, dict) for value in series.values()]
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if any(value not in (None, -999, -999.0) for value in flat_values):
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message="POWER reachable but recent values are not final yet")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


class OgimetAdapter(HttpSource):
    source_name = "OGIMET"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.url = "https://ogimet.com/gsynres.phtml.en"

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            text = await self._request_text(self.url)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            state = SourceState.OK if "WMO INDEX" in text.upper() else SourceState.DEGRADED
            return SourceHealth(source=self.source_name, state=state, latency_ms=latency, message="SYNOP daily summary form reachable")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))
