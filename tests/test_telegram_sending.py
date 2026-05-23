from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.commands import _reply_long
from src.bot.scheduler import _send_long


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
