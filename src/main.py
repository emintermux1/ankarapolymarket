from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date

from src.bot.telegram_bot import build_application
from src.config import get_settings, setup_logging
from src.db.repository import create_repository
from src.service import ForecastService


def main() -> None:
    parser = argparse.ArgumentParser(description="LTAC Ankara temperature intelligence bot")
    parser.add_argument("command", nargs="?", default="bot", choices=["bot", "web", "hourly", "report", "signals", "aviation", "forum", "sources", "result"])
    parser.add_argument("--date", dest="target_date", help="Target date as YYYY-MM-DD")
    parser.add_argument("--host", default="0.0.0.0", help="Web server host")
    parser.add_argument("--port", type=int, default=8000, help="Web server port")
    args = parser.parse_args()

    settings = get_settings()
    setup_logging(settings)
    repository = create_repository(settings)
    service = ForecastService(settings, repository)
    target = date.fromisoformat(args.target_date) if args.target_date else None

    if args.command == "report":
        print(asyncio.run(service.render_daily_report(target_date=target, report_label="cli")))
        return
    if args.command == "hourly":
        print(asyncio.run(service.render_hourly_max_forecast(target_date=target)))
        return
    if args.command == "signals":
        print(asyncio.run(service.render_advanced_signals(target_date=target)))
        return
    if args.command == "aviation":
        print(asyncio.run(service.render_aviation(target_date=target)))
        return
    if args.command == "forum":
        print(asyncio.run(service.render_forum(target_date=target)))
        return
    if args.command == "sources":
        print(asyncio.run(service.render_sources()))
        return
    if args.command == "result":
        print(asyncio.run(service.render_result(target_date=target)))
        return
    if args.command == "web":
        import uvicorn

        from src.web.app import create_app

        uvicorn.run(create_app(settings, repository, service), host=args.host, port=args.port)
        return

    application = build_application(settings, service)
    logging.getLogger(__name__).info("LTAC bot polling started")
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
