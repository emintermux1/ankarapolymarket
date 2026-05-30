from __future__ import annotations

import asyncio

import pytest

from src.bot.telegram_bot import _shutdown_scheduler, _start_scheduler, build_application
from src.config import Settings
from src.db.repository import create_repository
from src.service import ForecastService


def test_settings_accept_csv_and_json_list_values() -> None:
    settings = Settings(
        TELEGRAM_ADMIN_IDS="1374723312,42",
        POLYMARKET_TARGET_LOCATION_TERMS='["ankara","esenboğa","ltac"]',
        OPENMETEO_MODELS="ecmwf_ifs025,gfs_seamless",
        OPENMETEO_ENSEMBLE_MODELS='["ecmwf_ifs025","gfs_seamless"]',
    )

    assert settings.telegram_admin_ids == [1374723312, 42]
    assert settings.polymarket_target_location_terms == ["ankara", "esenboğa", "ltac"]
    assert settings.openmeteo_models == ["ecmwf_ifs025", "gfs_seamless"]
    assert settings.openmeteo_ensemble_models == ["ecmwf_ifs025", "gfs_seamless"]


def test_ankara_telegram_env_aliases_take_precedence() -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="wrong",
        TELEGRAM_CHANNEL_ID="@wrong",
        TELEGRAM_ADMIN_IDS="1",
        ANKARA_TELEGRAM_BOT_TOKEN="right",
        ANKARA_TELEGRAM_CHANNEL_ID="@ankarapm",
        ANKARA_TELEGRAM_ADMIN_IDS="1374723312",
        ANKARA_TELEGRAM_ALLOWED_CHAT_IDS="@ankarapm,1374723312",
    )

    assert settings.telegram_bot_token == "right"
    assert settings.telegram_channel_id == "@ankarapm"
    assert settings.telegram_admin_ids == [1374723312]
    assert settings.telegram_allowed_chat_keys == {"@ankarapm", "1374723312"}


def test_hourly_telegram_channel_mode_defaults() -> None:
    settings = Settings(
        TELEGRAM_ADMIN_IDS="",
        ANKARA_TELEGRAM_CHANNEL_ID="@ankarapm",
        ANKARA_TELEGRAM_HOURLY_FORECAST_CHANNEL_ID="@ankaraalerts",
        ANKARA_TELEGRAM_HOURLY_FORECAST_START_HOUR="7",
        ANKARA_TELEGRAM_HOURLY_FORECAST_END_HOUR="21",
        TELEGRAM_HOURLY_FORECAST_MINUTE="30",
    )

    assert settings.telegram_channel_mode_normalized == "hourly_max"
    assert settings.telegram_hourly_forecast_target_chat_id == "@ankaraalerts"
    assert settings.telegram_hourly_forecast_start_hour == 7
    assert settings.telegram_hourly_forecast_end_hour == 21
    assert settings.telegram_hourly_forecast_minute == 30
    assert "@ankaraalerts" in settings.telegram_allowed_chat_keys


def test_metar_alert_defaults_include_ltac_and_ltfm() -> None:
    settings = Settings(
        TELEGRAM_ADMIN_IDS="",
        ANKARA_TELEGRAM_CHANNEL_ID="@ankarapm",
        ANKARA_TELEGRAM_METAR_ALERT_CHANNEL_ID="@metaralarms",
        ANKARA_TELEGRAM_METAR_ALERT_STATION_IDS="ltac,ltfm,LTAC",
    )

    assert settings.telegram_metar_alerts_enabled is True
    assert settings.telegram_metar_alert_station_keys == ["LTAC", "LTFM"]
    assert settings.telegram_metar_alert_target_chat_id == "@metaralarms"
    assert "@metaralarms" in settings.telegram_allowed_chat_keys


def test_nearby_sensor_points_parse_istanbul_and_ankara_defaults() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="")
    points = settings.telegram_nearby_sensor_point_defs

    assert any(point["name"] == "Istanbul Airport / Arnavutköy" and point["region"] == "istanbul" for point in points)
    assert any(point["name"] == "Çubuk merkez" and point["region"] == "ankara" for point in points)
    assert all(isinstance(point["latitude"], float) and isinstance(point["longitude"], float) for point in points)


def test_telegram_channel_mode_aliases_are_normalized() -> None:
    assert (
        Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_CHANNEL_MODE="hourly").telegram_channel_mode_normalized
        == "hourly_max"
    )
    assert (
        Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_CHANNEL_MODE="legacy-reports").telegram_channel_mode_normalized
        == "legacy_reports"
    )
    assert Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_CHANNEL_MODE="both").telegram_channel_mode_normalized == "both"


def test_default_openmeteo_models_prioritize_ankara_sources() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="")

    assert settings.openmeteo_models == ["icon_eu", "ecmwf_ifs025", "icon_global", "gfs_seamless"]
    assert settings.openmeteo_ensemble_models == ["icon_eu", "ecmwf_ifs025", "icon_global", "gfs_seamless"]
    assert settings.openmeteo_bias_correction is True


@pytest.mark.asyncio
async def test_scheduler_starts_inside_running_loop(tmp_path) -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="123:abc",
        TELEGRAM_ADMIN_IDS="",
        DATABASE_URL=f"sqlite:///{tmp_path / 'test.db'}",
    )
    service = ForecastService(settings, create_repository(settings))
    application = build_application(settings, service)
    scheduler = application.bot_data["scheduler"]

    job_ids = {job.id for job in scheduler.get_jobs()}
    assert job_ids == {"ltac_hourly_max_forecast", "metar_sensor_alerts"}

    await _start_scheduler(application)
    assert scheduler.running

    await _shutdown_scheduler(application)
    await asyncio.sleep(0)
    assert not scheduler.running


def test_scheduler_legacy_mode_disables_hourly_job(tmp_path) -> None:
    settings = Settings(
        TELEGRAM_BOT_TOKEN="123:abc",
        TELEGRAM_ADMIN_IDS="",
        TELEGRAM_CHANNEL_MODE="legacy_reports",
        DATABASE_URL=f"sqlite:///{tmp_path / 'legacy.db'}",
    )
    service = ForecastService(settings, create_repository(settings))
    application = build_application(settings, service)

    job_ids = {job.id for job in application.bot_data["scheduler"].get_jobs()}
    assert "ltac_hourly_max_forecast" not in job_ids
    assert job_ids == {"ltac_alert_watch", "ltac_daily_forecast", "ltac_market_resolve", "metar_sensor_alerts"}
