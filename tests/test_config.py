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

    await _start_scheduler(application)
    assert scheduler.running

    await _shutdown_scheduler(application)
    await asyncio.sleep(0)
    assert not scheduler.running
