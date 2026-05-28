from __future__ import annotations

from telegram.ext import Application, CommandHandler

from src.bot import commands
from src.bot.scheduler import build_scheduler
from src.config import Settings
from src.service import ForecastService


def build_application(settings: Settings, service: ForecastService) -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_start_scheduler)
        .post_shutdown(_shutdown_scheduler)
        .build()
    )
    application.bot_data["service"] = service
    application.add_handler(CommandHandler("start", commands.start))
    application.add_handler(CommandHandler("hourly", commands.hourly))
    application.add_handler(CommandHandler("today", commands.today))
    application.add_handler(CommandHandler("now", commands.now))
    application.add_handler(CommandHandler("metar", commands.metar))
    application.add_handler(CommandHandler("taf", commands.taf))
    application.add_handler(CommandHandler("models", commands.models))
    application.add_handler(CommandHandler("forum", commands.forum))
    application.add_handler(CommandHandler("market", commands.market))
    application.add_handler(CommandHandler("edge", commands.edge))
    application.add_handler(CommandHandler("aviation", commands.aviation))
    application.add_handler(CommandHandler("ltac", commands.aviation))
    application.add_handler(CommandHandler("backtest", commands.backtest))
    application.add_handler(CommandHandler("sources", commands.sources))
    application.add_handler(CommandHandler("chart", commands.chart))
    application.add_handler(CommandHandler("result", commands.result))
    application.add_error_handler(commands.error_handler)
    scheduler = build_scheduler(application, service, settings)
    application.bot_data["scheduler"] = scheduler
    return application


async def _start_scheduler(application: Application) -> None:
    scheduler = application.bot_data["scheduler"]
    if not scheduler.running:
        scheduler.start()


async def _shutdown_scheduler(application: Application) -> None:
    scheduler = application.bot_data["scheduler"]
    if scheduler.running:
        scheduler.shutdown(wait=False)
