from __future__ import annotations

from src.data_sources.schemas import SourceHealth, SourceState


def unavailable_health() -> SourceHealth:
    return SourceHealth(source="Herbie_Optional", state=SourceState.UNAVAILABLE, message="GRIB ingestion intentionally deferred after MVP")

