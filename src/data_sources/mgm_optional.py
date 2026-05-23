from __future__ import annotations

from src.data_sources.schemas import SourceHealth, SourceState


def unavailable_health() -> SourceHealth:
    return SourceHealth(source="MGM_Optional", state=SourceState.UNAVAILABLE, message="MGM integration not enabled in MVP")

