from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from src.config import Settings
from src.data_sources.base import SourceError
from src.data_sources.aviation_watch import AviationWatchAdapter, _noaa_snapshot, _noaa_taf_snapshot
from src.data_sources.aviationweather import AviationWeatherAdapter
from src.data_sources.checkwx import CheckWXAdapter
from src.data_sources.copernicus import CopernicusAdapter
from src.data_sources.iem_asos import IEMASOSAdapter
from src.data_sources.met_no import MetNoAdapter
from src.data_sources.noaa_isd import NOAAISDAdapter
from src.data_sources.noaa_aviation import NOAAAviationAdapter
from src.data_sources.openmeteo import OpenMeteoAdapter
from src.data_sources.openmeteo_ecmwf import OpenMeteoECMWFAdapter
from src.data_sources.openmeteo_previous_runs import OpenMeteoPreviousRunsAdapter
from src.data_sources.openweather import OpenWeatherAdapter
from src.data_sources.rainviewer import RainViewerAdapter
from src.data_sources.tomorrow import TomorrowIOAdapter
from src.data_sources.visualcrossing import VisualCrossingAdapter
from src.data_sources.weatherapi_optional import WeatherAPIAdapter
from src.data_sources.weatherbit import WeatherbitAdapter
from src.data_sources.windy_optional import WindyAdapter
from src.service import ForecastService


@pytest.mark.asyncio
async def test_aviationweather_metar_accepts_ltfm_station(monkeypatch) -> None:
    adapter = AviationWeatherAdapter(Settings(TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert kwargs["params"]["ids"] == "LTFM"
        return [
            {
                "icaoId": "LTFM",
                "reportTime": "2026-05-27T10:05:00Z",
                "temp": 22,
                "dewp": 13,
                "wdir": 40,
                "wspd": 12,
                "wgst": 22,
                "altim": 1014,
                "visib": "6+",
                "clouds": [{"cover": "SCT", "base": 2500}],
                "rawOb": "METAR LTFM 271005Z 04012G22KT 9999 SCT025 22/13 Q1014",
            }
        ]

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    metar = await adapter.get_metar("ltfm")

    assert metar.station == "LTFM"
    assert metar.temperature_c == 22.0
    assert metar.wind_gust_kt == 22.0


def test_noaa_raw_metar_snapshot_parses_time_and_fingerprint() -> None:
    snapshot = _noaa_snapshot(
        "LTAC",
        "https://tgftp.nws.noaa.gov/data/observations/metar/stations/LTAC.TXT",
        "2026/05/30 09:20\nLTAC 300920Z VRB06KT 9999 SCT040 15/01 Q1018 NOSIG\n",
    )

    assert snapshot.source == "NOAA"
    assert snapshot.kind == "raw_metar_fast_fallback"
    assert snapshot.observed_at == datetime(2026, 5, 30, 9, 20, tzinfo=timezone.utc)
    assert snapshot.summary_lines == ["LTAC 300920Z VRB06KT 9999 SCT040 15/01 Q1018 NOSIG"]
    assert len(snapshot.fingerprint) == 16


def test_noaa_raw_taf_snapshot_parses_time_and_compacts_multiline_text() -> None:
    snapshot = _noaa_taf_snapshot(
        "LTAC",
        "https://tgftp.nws.noaa.gov/data/forecasts/taf/stations/LTAC.TXT",
        "2026/05/30 12:03\nTAF LTAC 301040Z 3012/3112 25012KT 9999 SCT040\n      BECMG 3015/3018 VRB02KT\n",
    )

    assert snapshot.source == "NOAA"
    assert snapshot.kind == "raw_taf_fast_fallback"
    assert snapshot.observed_at == datetime(2026, 5, 30, 12, 3, tzinfo=timezone.utc)
    assert snapshot.summary_lines == ["TAF LTAC 301040Z 3012/3112 25012KT 9999 SCT040 BECMG 3015/3018 VRB02KT"]


@pytest.mark.asyncio
async def test_aviation_watch_fetches_aviationweather_metar_snapshot(monkeypatch) -> None:
    adapter = AviationWatchAdapter(Settings(TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert url == "https://aviationweather.gov/api/data/metar"
        assert kwargs["params"] == {"ids": "LTAC", "format": "json"}
        return [
            {
                "icaoId": "LTAC",
                "reportTime": "2026-05-30T11:00:00Z",
                "temp": 15,
                "dewp": 2,
                "wdir": 260,
                "wspd": 7,
                "wgst": 17,
                "altim": 1017,
                "visib": "6+",
                "fltCat": "VFR",
                "clouds": [{"cover": "SCT", "base": 4000}],
                "rawOb": "METAR LTAC 301050Z 26007G17KT 9999 SCT040 15/02 Q1017",
            }
        ]

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    snapshot = await adapter.get_aviationweather_metar("ltac")

    assert snapshot.source == "AviationWeather"
    assert snapshot.kind == "official_metar_json"
    assert snapshot.observed_at == datetime(2026, 5, 30, 11, 0, tzinfo=timezone.utc)
    assert "wind 260/7G17kt" in snapshot.summary_lines[1]


@pytest.mark.asyncio
async def test_aviation_watch_fetches_aviationweather_taf_snapshot(monkeypatch) -> None:
    adapter = AviationWatchAdapter(Settings(TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert url == "https://aviationweather.gov/api/data/taf"
        assert kwargs["params"] == {"ids": "LTFM", "format": "json"}
        return [
            {
                "icaoId": "LTFM",
                "issueTime": "2026-05-30T10:40:00Z",
                "rawTAF": "TAF LTFM 301040Z 3012/3118 22017G27KT CAVOK",
                "fcsts": [
                    {
                        "timeFrom": 1780142400,
                        "timeTo": 1780160400,
                        "fcstChange": None,
                        "wdir": 220,
                        "wspd": 17,
                        "wgst": 27,
                        "wxString": "NSW",
                        "clouds": [{"cover": "NSC"}],
                    }
                ],
            }
        ]

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    snapshot = await adapter.get_aviationweather_taf("ltfm")

    assert snapshot.source == "AviationWeather"
    assert snapshot.kind == "official_taf_json"
    assert snapshot.observed_at == datetime(2026, 5, 30, 10, 40, tzinfo=timezone.utc)
    assert snapshot.summary_lines[0].startswith("TAF LTFM")
    assert any("wind 220/17G27kt" in line for line in snapshot.summary_lines)


@pytest.mark.asyncio
async def test_aviapages_uses_token_header_without_committing_token(monkeypatch) -> None:
    adapter = AviationWatchAdapter(Settings(TELEGRAM_ADMIN_IDS="", AVIAPAGES_API_TOKEN="test-token"))

    async def fake_request_json(url: str, **kwargs):
        assert url == "https://aviapages.com/api/v1/airports/LTAC/"
        assert kwargs["headers"] == {"Authorization": "Token test-token"}
        return {"icao": "LTAC", "name": "Ankara Esenboğa", "notams": [{"text": "RWY test NOTAM"}]}

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    snapshot = await adapter.get_aviapages_airport("ltac")

    assert snapshot.source == "Aviapages"
    assert "Ankara Esenboğa" in snapshot.title
    assert any("RWY test NOTAM" in line for line in snapshot.summary_lines)


@pytest.mark.asyncio
async def test_openmeteo_maps_professional_profile_fields(monkeypatch) -> None:
    adapter = OpenMeteoAdapter(Settings(TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        hourly = kwargs["params"]["hourly"]
        assert "wind_speed_250hPa" in hourly
        assert "temperature_925hPa" in hourly
        assert "soil_moisture_0_to_1cm" in hourly
        return {
            "latitude": 40.125,
            "longitude": 33.0,
            "elevation": 948,
            "hourly": {
                "time": ["2026-05-24T09:00", "2026-05-24T12:00"],
                "temperature_2m_icon_eu": [13.0, 22.0],
                "temperature_925hPa_icon_eu": [17.0, 19.0],
                "relative_humidity_700hPa_icon_eu": [78, 82],
                "temperature_850hPa_icon_eu": [10.0, 12.0],
                "geopotential_height_500hPa_icon_eu": [5710, 5720],
                "wind_speed_250hPa_icon_eu": [72, 84],
                "wind_direction_250hPa_icon_eu": [260, 270],
                "soil_temperature_0cm_icon_eu": [20.0, 24.0],
                "soil_moisture_0_to_1cm_icon_eu": [0.18, 0.16],
            },
        }

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    bundle = await adapter.get_forecast(date(2026, 5, 24))
    forecast = bundle.forecasts[0]

    assert forecast.available is True
    assert forecast.tmax_c == 22.0
    assert forecast.hourly[1].wind_speed_250hpa_kt == 84
    assert forecast.hourly[1].relative_humidity_700hpa_pct == 82
    assert forecast.hourly[1].soil_moisture_0_to_1cm_m3m3 == 0.16


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
async def test_openweather_forecast_adds_three_hour_fallback_model(monkeypatch) -> None:
    adapter = OpenWeatherAdapter(Settings(OPENWEATHER_API_KEY="test-key", TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert kwargs["params"]["appid"] == "test-key"
        assert kwargs["params"]["units"] == "metric"
        return {
            "list": [
                {
                    "dt_txt": "2026-05-24 12:00:00",
                    "main": {"temp": 22.4, "humidity": 42, "pressure": 1014},
                    "wind": {"speed": 4.0, "deg": 280},
                    "clouds": {"all": 30},
                    "rain": {"3h": 0.2},
                },
                {
                    "dt_txt": "2026-05-24 15:00:00",
                    "main": {"temp": 24.1, "humidity": 38},
                    "wind": {"speed": 5.0, "deg": 290},
                    "clouds": {"all": 15},
                },
            ],
        }

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    forecast = await adapter.get_model_forecast(date(2026, 5, 24))

    assert forecast.model == "openweather"
    assert forecast.tmax_c == 24.1
    assert forecast.hourly[0].precipitation_mm == 0.2
    assert round(forecast.hourly[0].wind_speed_10m_kt or 0, 1) == 7.8


@pytest.mark.asyncio
async def test_weatherbit_forecast_adds_daily_fallback_model(monkeypatch) -> None:
    adapter = WeatherbitAdapter(Settings(WEATHERBIT_API_KEY="test-key", TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert kwargs["params"]["key"] == "test-key"
        return {
            "data": [
                {
                    "valid_date": "2026-05-24",
                    "max_temp": 23.7,
                    "rh": 45,
                    "clouds": 20,
                    "precip": 0.0,
                    "wind_spd": 4.5,
                    "wind_dir": 275,
                }
            ]
        }

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    forecast = await adapter.get_model_forecast(date(2026, 5, 24))

    assert forecast.model == "weatherbit"
    assert forecast.tmax_c == 23.7
    assert forecast.hourly[0].cloud_cover_pct == 20


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


@pytest.mark.asyncio
async def test_met_no_forecast_adds_keyless_locationforecast_model(monkeypatch) -> None:
    adapter = MetNoAdapter(Settings(TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert kwargs["params"]["altitude"] == 953
        return {
            "properties": {
                "timeseries": [
                    {
                        "time": "2026-05-24T09:00:00Z",
                        "data": {
                            "instant": {
                                "details": {
                                    "air_temperature": 21.5,
                                    "relative_humidity": 42,
                                    "cloud_area_fraction": 30,
                                    "wind_speed": 4.0,
                                    "wind_from_direction": 270,
                                    "air_pressure_at_sea_level": 1014.2,
                                }
                            },
                            "next_1_hours": {"details": {"precipitation_amount": 0.1}},
                        },
                    }
                ]
            }
        }

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    forecast = await adapter.get_model_forecast(date(2026, 5, 24))

    assert forecast.model == "met_no"
    assert forecast.tmax_c == 21.5
    assert forecast.hourly[0].wind_speed_10m_kt == 7.78


@pytest.mark.asyncio
async def test_openmeteo_ecmwf_hres_maps_hourly_tmax(monkeypatch) -> None:
    adapter = OpenMeteoECMWFAdapter(Settings(TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert url.endswith("/v1/ecmwf")
        assert "temperature_2m_max" in kwargs["params"]["hourly"]
        return {
            "hourly": {
                "time": ["2026-05-24T12:00", "2026-05-24T15:00"],
                "temperature_2m": [22.0, 23.0],
                "temperature_2m_max": [22.2, 24.1],
                "cloud_cover": [30, 20],
                "shortwave_radiation": [700, 620],
            }
        }

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    forecast = await adapter.get_model_forecast(date(2026, 5, 24))

    assert forecast.model == "ecmwf_hres_9km"
    assert forecast.tmax_c == 24.1
    assert forecast.hourly[0].shortwave_radiation_wm2 == 700


@pytest.mark.asyncio
async def test_noaa_isd_daily_actual_uses_report_timezone_day(monkeypatch) -> None:
    adapter = NOAAISDAdapter(Settings(TELEGRAM_ADMIN_IDS=""))
    fetched_years = []

    async def fake_fetch_year(year: int):
        fetched_years.append(year)
        if year == 2025:
            return [{"DATE": "2025-12-31T22:30:00", "TMP": "+0260,1"}]
        if year == 2026:
            return [
                {"DATE": "2026-01-01T12:00:00", "TMP": "+0240,1"},
                {"DATE": "2026-01-01T21:30:00", "TMP": "+0300,1"},
            ]
        return []

    monkeypatch.setattr(adapter, "fetch_year", fake_fetch_year)

    result = await adapter.get_daily_actual(date(2026, 1, 1))

    assert fetched_years == [2025, 2026]
    assert result.source == "NOAA_ISD"
    assert result.tmax_c == 26.0
    assert result.rounded_tmax_c == 26


@pytest.mark.asyncio
async def test_openmeteo_previous_runs_collects_lead_time_values(monkeypatch) -> None:
    adapter = OpenMeteoPreviousRunsAdapter(Settings(OPENMETEO_MODELS="icon_eu", TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert "temperature_2m_previous_day2" in kwargs["params"]["hourly"]
        return {"hourly": {"temperature_2m_previous_day2_icon_eu": [20.0, None, 22.5]}}

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    values = await adapter.get_previous_day_temperatures(date(2026, 5, 24), lead_days=2)

    assert values == [20.0, 22.5]


@pytest.mark.asyncio
async def test_rainviewer_latest_tile_url_uses_ltac_coordinates(monkeypatch) -> None:
    adapter = RainViewerAdapter(Settings(TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        return {"host": "https://tiles.example", "radar": {"past": [{"path": "/v2/radar/test"}]}}

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    tile_url = await adapter.latest_radar_tile_url()

    assert tile_url == "https://tiles.example/v2/radar/test/512/7/40.1281/32.9951/2/1_1.png"


@pytest.mark.asyncio
async def test_noaa_pirep_radius_uses_kilometers_to_nautical_miles(monkeypatch) -> None:
    adapter = NOAAAviationAdapter(Settings(TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert kwargs["params"]["area"] == "108nm"
        return []

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    assert await adapter.get_pireps_near_ltac(radius_km=200.0) == []


@pytest.mark.asyncio
async def test_copernicus_submit_keeps_dates_as_date_objects(monkeypatch) -> None:
    adapter = CopernicusAdapter(Settings(COPERNICUS_CDS_API_KEY="test-key", TELEGRAM_ADMIN_IDS=""))
    captured_payload = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": []}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url: str, **kwargs):
            captured_payload.update(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    await adapter._retrieve_via_submit(date(2026, 5, 24), date(2026, 5, 25))

    assert captured_payload["day"] == ["24", "25"]


@pytest.mark.asyncio
async def test_aviation_enrichment_renderer_is_not_awaited() -> None:
    service = ForecastService.__new__(ForecastService)

    async def fake_metars(station):
        return ["metar"]

    async def fake_pireps(station):
        return "pireps"

    async def fake_flights(station):
        return "flights"

    service._fetch_aviation_metars = fake_metars
    service.render_pireps = fake_pireps
    service.render_flights = fake_flights
    service.windy_aviation = SimpleNamespace(radar_url_ltac=lambda: "radar", satellite_url_ltac=lambda: "satellite")
    service.noaa_aviation = SimpleNamespace(sigwx_url=lambda: "sigwx")
    service.renderer = SimpleNamespace(aviation_enrichment=lambda *args: "rendered")

    assert await service.render_aviation_enrichment("LTAC") == "rendered"


@pytest.mark.asyncio
async def test_optional_weatherapi_health_checks_configured_key(monkeypatch) -> None:
    adapter = WeatherAPIAdapter(Settings(WEATHERAPI_API_KEY="test-key", TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert kwargs["params"]["key"] == "test-key"
        return {"forecast": {"forecastday": []}}

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    health = await adapter.health()

    assert health.state.value == "ok"


@pytest.mark.asyncio
async def test_optional_windy_health_checks_configured_key(monkeypatch) -> None:
    adapter = WindyAdapter(Settings(WINDY_API_KEY="test-key", TELEGRAM_ADMIN_IDS=""))

    async def fake_request_json(url: str, **kwargs):
        assert kwargs["headers"] == {"X-WINDY-API-KEY": "test-key"}
        return {"ts": []}

    monkeypatch.setattr(adapter, "_request_json", fake_request_json)

    health = await adapter.health()

    assert health.state.value == "ok"


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
