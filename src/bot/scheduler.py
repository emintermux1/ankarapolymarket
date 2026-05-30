from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import LinkPreviewOptions
from telegram.ext import Application

from src.config import Settings
from src.data_sources.schemas import ForecastAnalysis, round_market_temperature_c
from src.service import ForecastService

logger = logging.getLogger(__name__)
_DISABLE_LINK_PREVIEWS = LinkPreviewOptions(is_disabled=True)
_DAILY_KEY = "telegram:daily_forecast"
_FORECAST_SNAPSHOT_KEY = "telegram:forecast_snapshot"
_MARKET_RESOLVE_KEY = "telegram:market_resolve"


def build_scheduler(application: Application, service: ForecastService, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.report_timezone))
    mode = settings.telegram_channel_mode_normalized
    if settings.telegram_metar_alerts_enabled:
        _add_metar_alert_job(scheduler, application, service, settings)
    if settings.telegram_aviation_source_watch_enabled:
        _add_aviation_source_watch_job(scheduler, application, service, settings)
    if settings.telegram_hourly_forecast_enabled and mode in {"hourly_max", "both"}:
        _add_hourly_forecast_job(scheduler, application, service, settings)
    if mode in {"legacy", "legacy_reports", "both"}:
        _add_daily_job(
            scheduler,
            settings.schedule_daily_report,
            _send_daily_forecast_alert,
            application,
            service,
            "daily_forecast",
        )
        _add_daily_job(
            scheduler,
            settings.schedule_result_report,
            _send_due_result_alerts,
            application,
            service,
            "market_resolve",
        )
        scheduler.add_job(
            _check_alerts,
            trigger="interval",
            minutes=settings.telegram_alert_check_interval_minutes,
            args=[application, service],
            id="ltac_alert_watch",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(ZoneInfo(settings.report_timezone)) + timedelta(minutes=1),
        )
    return scheduler


def _add_daily_job(
    scheduler: AsyncIOScheduler,
    time_text: str,
    func,
    application: Application,
    service: ForecastService,
    job_id: str,
) -> None:
    hour, minute = [int(part) for part in time_text.split(":", 1)]
    scheduler.add_job(
        func,
        trigger="cron",
        hour=hour,
        minute=minute,
        args=[application, service],
        id=f"ltac_{job_id}",
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


def _add_metar_alert_job(
    scheduler: AsyncIOScheduler,
    application: Application,
    service: ForecastService,
    settings: Settings,
) -> None:
    scheduler.add_job(
        _send_metar_alerts,
        trigger="interval",
        seconds=settings.telegram_metar_alert_interval_seconds,
        args=[application, service],
        id="metar_sensor_alerts",
        replace_existing=True,
        next_run_time=datetime.now(ZoneInfo(settings.report_timezone)),
        misfire_grace_time=120,
        coalesce=True,
        max_instances=1,
    )


def _add_aviation_source_watch_job(
    scheduler: AsyncIOScheduler,
    application: Application,
    service: ForecastService,
    settings: Settings,
) -> None:
    scheduler.add_job(
        _send_aviation_source_alerts,
        trigger="interval",
        seconds=settings.telegram_aviation_source_watch_interval_seconds,
        args=[application, service],
        id="aviation_source_watch",
        replace_existing=True,
        next_run_time=datetime.now(ZoneInfo(settings.report_timezone)) + timedelta(seconds=10),
        misfire_grace_time=120,
        coalesce=True,
        max_instances=1,
    )


async def _check_alerts(application: Application, service: ForecastService) -> None:
    if not service.settings.telegram_channel_id:
        logger.warning("telegram channel id is not configured")
        return
    now = datetime.now(ZoneInfo(service.settings.report_timezone))
    if _time_reached(now, service.settings.schedule_daily_report):
        await _send_daily_forecast_alert(application, service)
    await _send_forecast_change_alert(application, service)
    await _send_due_result_alerts(application, service)


async def _send_daily_forecast_alert(application: Application, service: ForecastService) -> None:
    if not service.settings.telegram_channel_id:
        logger.warning("telegram channel id is not configured")
        return
    target = service.default_target_date()
    if service.repository.notification_state(_state_key(_DAILY_KEY, target)):
        return
    ctx = await service.build_forecast_context(target_date=target, report_label="telegram_daily")
    text = service.renderer.daily_alert(ctx.analysis, ctx.market)
    await _send_long(application, service.settings.telegram_channel_id, text)
    payload = _forecast_snapshot_payload(ctx.analysis)
    service.repository.save_notification_state(_state_key(_DAILY_KEY, target), payload)
    service.repository.save_notification_state(_state_key(_FORECAST_SNAPSHOT_KEY, target), payload)


async def _send_forecast_change_alert(application: Application, service: ForecastService) -> None:
    if not service.settings.telegram_channel_id:
        logger.warning("telegram channel id is not configured")
        return
    target = service.default_target_date()
    if not service.repository.notification_state(_state_key(_DAILY_KEY, target)):
        return
    ctx = await service.build_forecast_context(target_date=target, report_label="telegram_change")
    current = _forecast_snapshot_payload(ctx.analysis)
    previous = service.repository.notification_state(_state_key(_FORECAST_SNAPSHOT_KEY, target))
    if previous is None:
        service.repository.save_notification_state(_state_key(_FORECAST_SNAPSHOT_KEY, target), current)
        return
    if not _forecast_changed(previous, current, service.settings.telegram_forecast_change_threshold_c):
        return
    text = service.renderer.forecast_change_alert(ctx.analysis, previous)
    await _send_long(application, service.settings.telegram_channel_id, text)
    service.repository.save_notification_state(_state_key(_FORECAST_SNAPSHOT_KEY, target), current)


async def _send_due_result_alerts(application: Application, service: ForecastService) -> None:
    if not service.settings.telegram_channel_id:
        logger.warning("telegram channel id is not configured")
        return
    now = datetime.now(ZoneInfo(service.settings.report_timezone))
    today = service.default_target_date()
    targets = [today, today - timedelta(days=1)]
    for target in targets:
        if target == today and not _time_reached(now, service.settings.schedule_result_report):
            continue
        await _send_market_resolve_alert(application, service, target)


async def _send_market_resolve_alert(application: Application, service: ForecastService, target: date) -> None:
    if service.repository.notification_state(_state_key(_MARKET_RESOLVE_KEY, target)):
        return
    result = await service.get_actual_result(target)
    if result.tmax_c is None or result.rounded_tmax_c is None:
        return
    text = service.renderer.market_resolve_alert(result)
    await _send_long(application, service.settings.telegram_channel_id, text)
    service.repository.save_notification_state(
        _state_key(_MARKET_RESOLVE_KEY, target),
        {
            "target_date": target.isoformat(),
            "tmax_c": result.tmax_c,
            "rounded_tmax_c": result.rounded_tmax_c,
            "source": result.source,
            "sent_at": datetime.now().isoformat(),
        },
    )


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


async def _send_metar_alerts(application: Application, service: ForecastService) -> None:
    chat_id = service.settings.telegram_metar_alert_target_chat_id
    if not chat_id:
        logger.warning("telegram METAR alert chat id is not configured")
        return
    max_age_minutes = service.settings.telegram_metar_alert_max_age_minutes
    for metar in await service.fetch_metar_alert_observations():
        if metar.age_minutes > max_age_minutes:
            logger.info("skipping stale %s METAR alert from %s", metar.station, metar.observation_time.isoformat())
            continue
        observed_at = metar.observation_time.astimezone(timezone.utc)
        key = f"telegram:metar-alert:{metar.station}:{observed_at.isoformat()}"
        if service.repository.telegram_delivery_exists(key):
            logger.info("%s METAR alert already sent for %s", metar.station, observed_at.isoformat())
            continue
        text = await service.render_metar_alert(metar)
        await _send_long(application, chat_id, text)
        service.repository.save_telegram_delivery(
            key=key,
            chat_id=str(chat_id),
            kind="metar_alert",
            target_date=observed_at.date(),
            scheduled_for=observed_at,
            payload={
                "station": metar.station,
                "source": metar.source,
                "raw": metar.raw_text,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            },
        )


async def _send_aviation_source_alerts(application: Application, service: ForecastService) -> None:
    chat_id = service.settings.telegram_aviation_source_watch_target_chat_id
    if not chat_id:
        logger.warning("telegram aviation source watch chat id is not configured")
        return
    now = datetime.now(timezone.utc)
    snapshots = await service.fetch_aviation_source_snapshots()
    new_snapshots = []
    new_keys = set()
    for snapshot in snapshots:
        key = f"telegram:aviation-source:{snapshot.station}:{snapshot.source}:{snapshot.kind}:{snapshot.fingerprint}"
        if service.repository.telegram_delivery_exists(key):
            logger.info("%s %s source alert already sent for %s", snapshot.station, snapshot.source, snapshot.fingerprint)
            continue
        new_snapshots.append(snapshot)
        new_keys.add(key)
    if not new_snapshots:
        logger.info("aviation source watch found no new fingerprints")
        return
    text = await service.render_aviation_source_digest(snapshots, new_keys)
    try:
        await _send_long(application, chat_id, text)
    except Exception as exc:
        logger.warning("failed to send aviation source digest: %s", exc)
        return
    for snapshot in new_snapshots:
        key = f"telegram:aviation-source:{snapshot.station}:{snapshot.source}:{snapshot.kind}:{snapshot.fingerprint}"
        service.repository.save_telegram_delivery(
            key=key,
            chat_id=str(chat_id),
            kind="aviation_source_digest",
            target_date=now.date(),
            scheduled_for=now,
            payload={
                "station": snapshot.station,
                "source": snapshot.source,
                "kind": snapshot.kind,
                "fingerprint": snapshot.fingerprint,
                "source_url": snapshot.source_url,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            },
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


def _state_key(prefix: str, target: date) -> str:
    return f"{prefix}:{target.isoformat()}"


def _forecast_snapshot_payload(analysis: ForecastAnalysis) -> dict:
    rounded = round_market_temperature_c(analysis.final_tmax_c) if analysis.final_tmax_c is not None else None
    return {
        "target_date": analysis.target_date.isoformat(),
        "final_tmax_c": analysis.final_tmax_c,
        "rounded_tmax_c": rounded,
        "confidence_score": analysis.confidence_score,
        "range_low_c": analysis.main_range_low_c,
        "range_high_c": analysis.main_range_high_c,
        "generated_at": analysis.generated_at.isoformat(),
        "sent_at": datetime.now().isoformat(),
    }


def _forecast_changed(previous: dict, current: dict, threshold_c: float) -> bool:
    previous_tmax = _safe_float(previous.get("final_tmax_c"))
    current_tmax = _safe_float(current.get("final_tmax_c"))
    if previous_tmax is None or current_tmax is None:
        return False
    if previous.get("rounded_tmax_c") != current.get("rounded_tmax_c"):
        return True
    return abs(current_tmax - previous_tmax) >= threshold_c


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _time_reached(now: datetime, time_text: str) -> bool:
    hour, minute = [int(part) for part in time_text.split(":", 1)]
    return (now.hour, now.minute) >= (hour, minute)
