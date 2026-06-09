from __future__ import annotations

from datetime import datetime, timezone

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import SourceHealth, SourceState


class CopernicusAdapter(HttpSource):
    """Copernicus Climate Data Store adapter (requires CDS API key)."""

    source_name = "Copernicus"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def health(self) -> SourceHealth:
        if not self.settings.copernicus_cds_api_key:
            return SourceHealth(
                source=self.source_name,
                state=SourceState.UNAVAILABLE,
                message="COPERNICUS_CDS_API_KEY not configured",
            )
        return SourceHealth(
            source=self.source_name,
            state=SourceState.DEGRADED,
            message="CDS API key configured; dataset retrieval not implemented in MVP",
        )


def copernicus_unavailable_health() -> SourceHealth:
    return SourceHealth(
        source="Copernicus",
        state=SourceState.UNAVAILABLE,
        message="COPERNICUS_CDS_API_KEY not configured",
    )
