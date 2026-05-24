from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.config import Settings
from src.data_sources.base import SourceError
from src.data_sources.checkwx import CheckWXAdapter
from src.data_sources.iem_asos import IEMASOSAdapter
from src.data_sources.rainviewer import RainViewerRadarAdapter
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
async def test_iem_intraday_high_tracks_reported_metar_integer_peak(monkeypatch) -> None:
    adapter = IEMASOSAdapter(Settings(TELEGRAM_ADMIN_IDS=""))
    as_of = datetime(2026, 5, 24, 12, 45, tzinfo=timezone.utc)

    async def fake_fetch_history(start_at, end_at):
        assert start_at.date() == date(2026, 5, 24)
        assert end_at.astimezone(timezone.utc) == as_of
        return [
            {"valid": "2026-05-24 10:20", "tmpc": "20.0", "metar": "LTAC 241020Z 20/08"},
            {"valid": "2026-05-24 11:20", "tmpc": "21.0", "metar": "LTAC 241120Z 21/08"},
        ]

    monkeypatch.setattr(adapter, "fetch_history", fake_fetch_history)

    result = await adapter.get_intraday_high(date(2026, 5, 24), as_of=as_of)

    assert result.source == "IEM_ASOS_intraday"
    assert result.tmax_c == 21.0
    assert result.rounded_tmax_c == 21
    assert result.raw_payload["max_metar"] == "LTAC 241120Z 21/08"


@pytest.mark.asyncio
async def test_rainviewer_radar_motion_samples_upwind_tile(monkeypatch) -> None:
    adapter = RainViewerRadarAdapter(Settings(TELEGRAM_ADMIN_IDS=""))
    calls = []

    async def fake_request_json(url: str, **kwargs):
        return {
            "host": "https://tilecache.rainviewer.com",
            "radar": {
                "past": [
                    {"time": 1_779_600_000, "path": "/v2/radar/1779600000"},
                    {"time": 1_779_600_600, "path": "/v2/radar/1779600600"},
                ]
            },
        }

    async def fake_request_bytes(url: str, **kwargs):
        calls.append(url)
        return _radar_png(west_alpha=0.55 if "1779600600" in url else 0.0)

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)
    monkeypatch.setattr(adapter, "_request_bytes", fake_request_bytes)

    signal = await adapter.get_radar_motion(wind_direction_deg=270)

    assert len(calls) == 2
    assert "/40.1281/32.9951/" in calls[-1]
    assert signal.motion == "approaching"
    assert signal.upwind_intensity > signal.center_intensity


def _radar_png(*, west_alpha: float) -> bytes:
    from io import BytesIO

    import matplotlib.image as mpimg
    import numpy as np

    image = np.zeros((256, 256, 4), dtype=float)
    image[92:164, 18:92, 3] = west_alpha
    buf = BytesIO()
    mpimg.imsave(buf, image, format="png")
    return buf.getvalue()
