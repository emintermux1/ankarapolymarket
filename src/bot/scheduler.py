from __future__ import annotations

import logging
from datetime import date, datetime, timezone
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
    mode = settings.telegram_channel_mode_normalized
    if settings.telegram_hourly_forecast_enabled and mode in {"hourly_max", "both"}:
        _add_hourly_forecast_job(scheduler, application, service, settings)
    if mode in {"legacy", "legacy_reports", "both"}:
        _add_daily_job(scheduler, settings.schedule_daily_report, _send_daily_report, application, service, "09:00")
        _add_daily_job(scheduler, settings.schedule_midday_update, _send_daily_report, application, service, "12:00")
        _add_daily_job(scheduler, settings.schedule_risk_update, _send_daily_report, application, service, "15:00")
        _add_daily_job(scheduler, settings.schedule_result_report, _send_result_report, application, service, "21:00")
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


def _add_hourly_forecast_job(
    scheduler: AsyncIOScheduler,
    application: Application,
    service: ForecastService,
    settings: Settings,
) -> None:
    scheduler.add_job(
        _send_hourly_max_forecast,
        trigger="cron",
        hour=_hour_range(settings.telegram_hourly_forecast_start_hour, settings.telegram_hourly_forecast_end_hour),
        minute=settings.telegram_hourly_forecast_minute,
        args=[application, service],
        id="ltac_hourly_max_forecast",
        replace_existing=True,
        misfire_grace_time=900,
        coalesce=True,
        max_instances=1,
    )


async def _send_daily_report(application: Application, service: ForecastService, label: str) -> None:
    if not service.settings.telegram_channel_id:
        logger.warning("telegram channel id is not configured")
        return
    text = await service.render_daily_report(report_label=label)
    await _send_long(application, service.settings.telegram_channel_id, text)


async def _send_result_report(application: Application, service: ForecastService, label: str) -> None:
    if not service.settings.telegram_channel_id:
        logger.warning("telegram channel id is not configured")
        return
    text = await service.render_result()
    await _send_long(application, service.settings.telegram_channel_id, text)


async def _send_hourly_max_forecast(application: Application, service: ForecastService) -> None:
    chat_id = service.settings.telegram_hourly_forecast_target_chat_id
    if not chat_id:
        logger.warning("telegram hourly forecast chat id is not configured")
        return
    tz = ZoneInfo(service.settings.report_timezone)
    now = datetime.now(tz)
    label = f"hourly-{now:%H}"
    key = f"telegram:hourly-max:{now.date().isoformat()}:{now:%H}"
    if service.repository.telegram_delivery_exists(key):
        logger.info("telegram hourly max forecast already sent for %s", key)
        return
    text = await service.render_hourly_max_forecast(target_date=now.date(), report_label=label)
    await _send_long(application, chat_id, text)
    service.repository.save_telegram_delivery(
        key=key,
        chat_id=str(chat_id),
        kind="hourly_max_forecast",
        target_date=now.date(),
        scheduled_for=now.replace(minute=service.settings.telegram_hourly_forecast_minute, second=0, microsecond=0),
        payload={"label": label, "sent_at": datetime.now(timezone.utc).isoformat()},
    )


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


def _hour_range(start_hour: int, end_hour: int) -> str:
    if start_hour <= end_hour:
        return f"{start_hour}-{end_hour}"
    return f"{start_hour}-23,0-{end_hour}"
