from __future__ import annotations

import logging
from datetime import date
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import LinkPreviewOptions
from telegram.ext import Application

from src.config import Settings
from src.service import ForecastService

logger = logging.getLogger(__name__)
_DISABLE_LINK_PREVIEWS = LinkPreviewOptions(is_disabled=True)


def build_scheduler(application: Application, service: ForecastService, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.report_timezone))
    scheduler.add_job(
        _send_hourly_forecast,
        trigger="cron",
        minute=settings.schedule_hourly_forecast_minute,
        args=[application, service],
        id="ltac_hourly_max_forecast",
        replace_existing=True,
        misfire_grace_time=900,
    )
    return scheduler


def _add_daily_job(
    scheduler: AsyncIOScheduler,
    time_text: str,
    func,
    application: Application,
    service: ForecastService,
    label: str,
) -> None:
    hour, minute = [int(part) for part in time_text.split(":", 1)]
    scheduler.add_job(
        func,
        trigger="cron",
        hour=hour,
        minute=minute,
        args=[application, service, label],
        id=f"ltac_{label.replace(':', '')}",
        replace_existing=True,
        misfire_grace_time=900,
    )


async def _send_daily_report(application: Application, service: ForecastService, label: str) -> None:
    if not service.settings.telegram_channel_id:
        logger.warning("telegram channel id is not configured")
        return
    text = await service.render_daily_report(report_label=label)
    await _send_long(application, service.settings.telegram_channel_id, text)


async def _send_hourly_forecast(application: Application, service: ForecastService) -> None:
    if not service.settings.telegram_channel_id:
        logger.warning("telegram channel id is not configured")
        return
    text = await service.render_hourly_max_forecast()
    await _send_long(application, service.settings.telegram_channel_id, text)


async def _send_result_report(application: Application, service: ForecastService, label: str) -> None:
    if not service.settings.telegram_channel_id:
        logger.warning("telegram channel id is not configured")
        return
    text = await service.render_result()
    await _send_long(application, service.settings.telegram_channel_id, text)


async def _send_long(application: Application, chat_id: str, text: str) -> None:
    while text:
        chunk = text[:3900]
        if len(text) > 3900:
            split_at = chunk.rfind("\n")
            if split_at > 0:
                chunk = chunk[:split_at]
        await application.bot.send_message(
            chat_id=chat_id,
            text=chunk,
            link_preview_options=_DISABLE_LINK_PREVIEWS,
        )
        text = text[len(chunk):].lstrip()
