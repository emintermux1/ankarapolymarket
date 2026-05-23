from __future__ import annotations

from src.data_sources.schemas import SourceHealth, SourceState


def unavailable_health() -> SourceHealth:
    return SourceHealth(source="Meteoblue_Optional", state=SourceState.UNAVAILABLE, message="METEOBLUE_API_KEY not configured")

