from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.commands import _reply_long
from src.bot.scheduler import _send_long, _send_metar_alerts
from src.data_sources.schemas import METARNormalized


@pytest.mark.asyncio
async def test_command_replies_disable_link_previews() -> None:
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message)

    await _reply_long(update, "Market: https://polymarket.com/event/test")

    kwargs = message.reply_text.call_args.kwargs
    assert kwargs["parse_mode"] is None
    assert kwargs["link_preview_options"].to_dict() == {"is_disabled": True}


@pytest.mark.asyncio
async def test_scheduled_channel_posts_disable_link_previews() -> None:
    bot = SimpleNamespace(send_message=AsyncMock())
    application = SimpleNamespace(bot=bot)

    await _send_long(application, "@ankarapm", "Market: https://polymarket.com/event/test")

    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == "@ankarapm"
    assert kwargs["link_preview_options"].to_dict() == {"is_disabled": True}


@pytest.mark.asyncio
async def test_metar_alert_sends_new_station_observation_once() -> None:
    now = datetime.now(timezone.utc)
    metar = METARNormalized(
        source="AviationWeather",
        station="LTFM",
        fetch_timestamp=now,
        observation_time=now,
        temperature_c=22.0,
        dew_point_c=13.0,
        relative_humidity=57,
        wind_direction_deg=40,
        wind_speed_kt=12.0,
        wind_gust_kt=22.0,
        pressure_hpa=1014.0,
        visibility_m=9999,
        cloud_layers=[],
        raw_text="METAR LTFM 271005Z 04012G22KT 9999 SCT025 22/13 Q1014",
    )
    repository = _FakeRepository()
    service = SimpleNamespace(
        settings=SimpleNamespace(
            telegram_metar_alert_target_chat_id="@ankarapm",
            telegram_metar_alert_max_age_minutes=180,
        ),
        repository=repository,
        fetch_metar_alert_observations=AsyncMock(return_value=[metar]),
        render_metar_alert=AsyncMock(return_value="LTFM alert"),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    application = SimpleNamespace(bot=bot)

    await _send_metar_alerts(application, service)
    await _send_metar_alerts(application, service)

    assert bot.send_message.call_count == 1
    assert bot.send_message.call_args.kwargs["text"] == "LTFM alert"
    assert repository.saved[0]["kind"] == "metar_alert"
    assert "LTFM" in repository.saved[0]["key"]


class _FakeRepository:
    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.saved: list[dict] = []

    def telegram_delivery_exists(self, key: str) -> bool:
        return key in self.keys

    def save_telegram_delivery(self, **kwargs) -> bool:
        self.keys.add(kwargs["key"])
        self.saved.append(kwargs)
        return True
