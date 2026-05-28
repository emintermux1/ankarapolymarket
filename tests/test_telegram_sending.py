from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.commands import _reply_long
from src.bot.scheduler import _send_hourly_forecast, _send_long, build_scheduler
from src.config import Settings


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


def test_scheduler_only_posts_hourly_max_forecast_to_channel() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", SCHEDULE_HOURLY_FORECAST_MINUTE=17)
    application = SimpleNamespace()
    service = SimpleNamespace()

    scheduler = build_scheduler(application, service, settings)

    jobs = scheduler.get_jobs()
    assert [job.id for job in jobs] == ["ltac_hourly_max_forecast"]
    assert "minute='17'" in str(jobs[0].trigger)


@pytest.mark.asyncio
async def test_hourly_scheduler_sends_compact_forecast() -> None:
    bot = SimpleNamespace(send_message=AsyncMock())
    application = SimpleNamespace(bot=bot)
    service = SimpleNamespace(
        settings=SimpleNamespace(telegram_channel_id="@ankarapm"),
        render_hourly_max_forecast=AsyncMock(return_value="ANKARA LTAC SAAT BAŞI MAKS TAHMİN\nBugünün beklenen en yüksek sıcaklığı: 24.5°C"),
    )

    await _send_hourly_forecast(application, service)

    service.render_hourly_max_forecast.assert_awaited_once()
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == "@ankarapm"
    assert "SAAT BAŞI MAKS TAHMİN" in kwargs["text"]
