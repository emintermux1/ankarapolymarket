from __future__ import annotations

from datetime import date
from telegram import LinkPreviewOptions, Update
from telegram.ext import ContextTypes

from src.service import ForecastService

_DISABLE_LINK_PREVIEWS = LinkPreviewOptions(is_disabled=True)


def service_from_context(context: ContextTypes.DEFAULT_TYPE) -> ForecastService:
    return context.application.bot_data["service"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply(
        update,
        "🇹🇷 LTAC Ankara Esenboğa bot aktif.\n\n"
        "🌡️ Tahmin: /hourly /today /aviation /ltac /now /metar /metars /taf /models /signals /market /edge\n"
        "🌍 Çevre: /mgm /aqi /uv /baraj /cevre /env /radar\n"
        "⚡ Altyapı: /outage /kesinti /twitter\n"
        "📊 Diğer: /backtest /sources /chart /result",
    )


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    target = _parse_date_arg(context.args) if context.args else None
    await _reply_long(update, await service.render_daily_report(target_date=target, report_label="command"))


async def hourly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    target = _parse_date_arg(context.args) if context.args else None
    await _reply_long(update, await service.render_hourly_max_forecast(target_date=target, report_label="command-hourly"))


async def now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    station = context.args[0].upper() if context.args else None
    await _reply_long(update, await service.render_now(station=station))


async def metar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await now(update, context)


async def metars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    metars = await service.fetch_metar_alert_observations()
    if not metars:
        await _reply_long(update, "LTAC/LTFM METAR verisi yok.")
        return
    reports = [await service.render_metar_alert(metar) for metar in metars]
    await _reply_long(update, "\n\n".join(reports))


async def taf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply_long(update, await service.render_taf())


async def models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    target = _parse_date_arg(context.args) if context.args else None
    await _reply_long(update, await service.render_models(target_date=target))


async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    target = _parse_date_arg(context.args) if context.args else None
    await _reply_long(update, await service.render_advanced_signals(target_date=target))


async def market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    target = _parse_date_arg(context.args) if context.args else None
    await _reply_long(update, await service.render_market(target_date=target))


async def forum(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    target = _parse_date_arg(context.args) if context.args else None
    await _reply_long(update, await service.render_forum(target_date=target))


async def edge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    target = _parse_date_arg(context.args) if context.args else None
    await _reply_long(update, await service.render_edge(target_date=target))


async def aviation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    target = _parse_date_arg(context.args) if context.args else None
    await _reply_long(update, await service.render_aviation(target_date=target))


async def backtest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply_long(update, service.render_backtest())


async def sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply_long(update, await service.render_sources())


async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    target = _parse_date_arg(context.args) if context.args else None
    path, caption = await service.model_chart(target_date=target)
    if update.effective_chat:
        with open(path, "rb") as image:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=image, caption=caption)


async def mgm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply_long(update, await service.render_mgm())


async def aqi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply_long(update, await service.render_aqi())


async def uv_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply_long(update, await service.render_uv())


async def baraj(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply_long(update, await service.render_baraj())


async def cevre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply_long(update, await service.render_environment_digest())


async def env_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cevre(update, context)


async def radar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    url, caption = await service.render_radar()
    if update.effective_chat:
        if url:
            await _reply(update, f"{caption}\n\n{url}")
        else:
            await _reply(update, caption)


async def outage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply_long(update, await service.render_outages())


async def kesinti(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await outage(update, context)


async def twitter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    await _reply_long(update, await service.render_twitter())


async def result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = service_from_context(context)
    if not _is_allowed_chat(update, service):
        return
    if context.args and _is_admin(update, service):
        try:
            tmax = float(context.args[0].replace(",", "."))
            target = _parse_date_arg(context.args[1:]) if len(context.args) > 1 else service.default_target_date()
        except ValueError:
            await _reply(update, "Kullanım: /result 18.0 2026-05-24")
            return
        await _reply_long(update, service.save_manual_result(target, tmax))
        return
    target = _parse_date_arg(context.args) if context.args else None
    await _reply_long(update, await service.render_result(target_date=target))


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = context.application.bot_data.get("service")
    if isinstance(update, Update) and isinstance(service, ForecastService) and not _is_allowed_chat(update, service):
        return
    if isinstance(update, Update) and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Hata: {context.error}",
            link_preview_options=_DISABLE_LINK_PREVIEWS,
        )


def _parse_date_arg(args: list[str]) -> date:
    if not args:
        raise ValueError("date argument missing")
    return date.fromisoformat(args[0])


def _is_admin(update: Update, service: ForecastService) -> bool:
    user = update.effective_user
    return bool(user and user.id in service.settings.telegram_admin_ids)


def _is_allowed_chat(update: Update, service: ForecastService) -> bool:
    settings = service.settings
    if not settings.telegram_restrict_commands:
        return True
    allowed = settings.telegram_allowed_chat_keys
    if not allowed:
        return True
    candidates: set[str] = set()
    if update.effective_chat:
        candidates.add(str(update.effective_chat.id).lower())
        username = update.effective_chat.username
        if username:
            candidates.add(username.lower())
            candidates.add(f"@{username}".lower())
    if update.effective_user:
        candidates.add(str(update.effective_user.id).lower())
    return bool(candidates & allowed)


async def _reply(update: Update, text: str) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(text, link_preview_options=_DISABLE_LINK_PREVIEWS)


async def _reply_long(update: Update, text: str) -> None:
    if update.effective_message is None:
        return
    for chunk in _chunks(text, 3900):
        await update.effective_message.reply_text(
            chunk,
            parse_mode=None,
            link_preview_options=_DISABLE_LINK_PREVIEWS,
        )


def _chunks(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts = []
    remaining = text
    while len(remaining) > max_len:
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts
