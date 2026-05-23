from __future__ import annotations

from src.data_sources.schemas import SourceHealth, SourceState


def unavailable_health() -> SourceHealth:
    return SourceHealth(source="WeatherAPI_Optional", state=SourceState.UNAVAILABLE, message="WEATHERAPI_API_KEY not configured")

