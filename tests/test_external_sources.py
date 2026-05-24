from __future__ import annotations

from datetime import date

import pytest

from src.config import Settings
from src.data_sources.base import SourceError
from src.data_sources.checkwx import CheckWXAdapter
from src.data_sources.openmeteo import OpenMeteoAdapter
from src.data_sources.tomorrow import TomorrowIOAdapter
from src.data_sources.visualcrossing import VisualCrossingAdapter


@pytest.mark.asyncio
async def test_visualcrossing_forecast_uses_configured_timeline_payload(monkeypatch) -> None:
    adapter = VisualCrossingAdapter(Settings(VISUALCROSSING_API_KEY="test-key", TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert "ankara%20esenbo" in url
        assert kwargs["params"]["key"] == "test-key"
        return {
            "resolvedAddress": "Esenboğa",
            "days": [{"datetime": "2026-05-24", "tempmax": 21.4, "source": "fcst"}],
        }

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    forecast = await adapter.get_model_forecast(date(2026, 5, 24))
    result = await adapter.get_daily_result(date(2026, 5, 24))

    assert forecast.model == "visual_crossing"
    assert forecast.tmax_c == 21.4
    assert result.tmax_c == 21.4
    assert result.rounded_tmax_c == 21


@pytest.mark.asyncio
async def test_checkwx_requires_api_key() -> None:
    adapter = CheckWXAdapter(Settings(TELEGRAM_ADMIN_IDS=""))

    with pytest.raises(SourceError):
        await adapter.get_metar()


@pytest.mark.asyncio
async def test_tomorrow_forecast_maps_cloud_ceiling_and_radiation(monkeypatch) -> None:
    adapter = TomorrowIOAdapter(Settings(TOMORROW_API_KEY="test-key", TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert kwargs["params"]["apikey"] == "test-key"
        return {
            "timelines": {
                "hourly": [
                    {
                        "time": "2026-05-24T10:00:00Z",
                        "values": {
                            "temperature": 20.5,
                            "cloudCover": 75,
                            "cloudBase": 1.2,
                            "cloudCeiling": 2.4,
                            "solarGHI": 520,
                            "windSpeed": 6,
                            "windDirection": 40,
                        },
                    }
                ]
            }
        }

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    forecast = await adapter.get_model_forecast(date(2026, 5, 24))

    assert forecast.model == "tomorrow_io"
    assert forecast.tmax_c == 20.5
    assert forecast.hourly[0].cloud_cover_low_pct == 75
    assert forecast.hourly[0].cloud_base_m == 1200
    assert forecast.hourly[0].cloud_ceiling_m == 2400
    assert forecast.hourly[0].shortwave_radiation_wm2 == 520


@pytest.mark.asyncio
async def test_openmeteo_forecast_maps_cape_and_cin(monkeypatch) -> None:
    adapter = OpenMeteoAdapter(Settings(OPENMETEO_MODELS="gfs_seamless", TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert "convective_inhibition" in kwargs["params"]["hourly"]
        return {
            "latitude": 40.125,
            "longitude": 33.0,
            "elevation": 948,
            "hourly": {
                "time": ["2026-05-24T15:00"],
                "temperature_2m_gfs_seamless": [22.0],
                "cape_gfs_seamless": [850],
                "convective_inhibition_gfs_seamless": [125],
            },
        }

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    bundle = await adapter.get_forecast(date(2026, 5, 24))

    point = bundle.forecasts[0].hourly[0]
    assert point.cape_jkg == 850
    assert point.convective_inhibition_jkg == 125
