from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.commands import _reply_long
from src.bot.scheduler import _send_aviation_source_alerts, _send_long, _send_metar_alerts, _send_power_outage_alerts, _send_twitter_posts
from src.data_sources.schemas import AviationSourceSnapshot, METARNormalized


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


@pytest.mark.asyncio
async def test_aviation_source_watch_sends_digest_every_run_and_marks_only_new_fingerprints() -> None:
    now = datetime.now(timezone.utc)
    snapshots = [
        AviationSourceSnapshot(
            source="NOAA",
            station="LTAC",
            kind="raw_metar_fast_fallback",
            title="LTAC NOAA raw METAR",
            source_url="https://tgftp.nws.noaa.gov/data/observations/metar/stations/LTAC.TXT",
            fetch_timestamp=now,
            observed_at=now,
            summary_lines=["LTAC 300920Z VRB06KT 9999 SCT040 15/01 Q1018 NOSIG"],
            fingerprint="abc123",
        ),
        AviationSourceSnapshot(
            source="IFATC",
            station="LTAC",
            kind="airport_runway_frequency_metadata",
            title="LTAC IFATC airport info",
            source_url="https://www.ifatc.org/airports?apt=LTAC",
            fetch_timestamp=now,
            summary_lines=["Tower 118.1"],
            fingerprint="def456",
        ),
    ]
    repository = _FakeRepository()
    service = SimpleNamespace(
        settings=SimpleNamespace(telegram_aviation_source_watch_target_chat_id="@ankarapm"),
        repository=repository,
        fetch_aviation_source_snapshots=AsyncMock(return_value=snapshots),
        render_aviation_source_digest=AsyncMock(return_value="single digest"),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    application = SimpleNamespace(bot=bot)

    await _send_aviation_source_alerts(application, service)
    await _send_aviation_source_alerts(application, service)

    assert bot.send_message.call_count == 2
    assert bot.send_message.call_args.kwargs["text"] == "single digest"
    assert service.render_aviation_source_digest.call_args_list[0].args == (
        snapshots,
        {
            "telegram:aviation-source:LTAC:NOAA:raw_metar_fast_fallback:abc123",
            "telegram:aviation-source:LTAC:IFATC:airport_runway_frequency_metadata:def456",
        },
    )
    assert service.render_aviation_source_digest.call_args_list[1].args == (snapshots, set())
    assert [item["kind"] for item in repository.saved] == ["aviation_source_digest", "aviation_source_digest"]
    assert {"abc123", "def456"} == {item["payload"]["fingerprint"] for item in repository.saved}


@pytest.mark.asyncio
async def test_aviation_source_watch_does_not_mark_digest_delivered_after_send_timeout() -> None:
    now = datetime.now(timezone.utc)
    snapshots = [
        AviationSourceSnapshot(
            source="NOAA",
            station="LTAC",
            kind="raw_metar_fast_fallback",
            title="LTAC NOAA raw METAR",
            source_url="https://tgftp.nws.noaa.gov/data/observations/metar/stations/LTAC.TXT",
            fetch_timestamp=now,
            summary_lines=["LTAC first"],
            fingerprint="first",
        ),
        AviationSourceSnapshot(
            source="IFATC",
            station="LTAC",
            kind="airport_runway_frequency_metadata",
            title="LTAC IFATC airport info",
            source_url="https://www.ifatc.org/airports?apt=LTAC",
            fetch_timestamp=now,
            summary_lines=["LTAC second"],
            fingerprint="second",
        ),
    ]
    repository = _FakeRepository()
    service = SimpleNamespace(
        settings=SimpleNamespace(telegram_aviation_source_watch_target_chat_id="@ankarapm"),
        repository=repository,
        fetch_aviation_source_snapshots=AsyncMock(return_value=snapshots),
        render_aviation_source_digest=AsyncMock(return_value="single digest"),
    )
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError("slow")))
    application = SimpleNamespace(bot=bot)

    await _send_aviation_source_alerts(application, service)

    assert bot.send_message.call_count == 1
    assert repository.saved == []


@pytest.mark.asyncio
async def test_twitter_posts_mark_delivered_after_send_success() -> None:
    post = SimpleNamespace(post_id="p1", author="mgm", published_at=datetime.now(timezone.utc))
    repository = _FakeRepository()
    service = SimpleNamespace(
        settings=SimpleNamespace(telegram_channel_id="@ankarapm"),
        repository=repository,
        twitter=SimpleNamespace(get_twitter_snapshots=AsyncMock(return_value=[post])),
        renderer=SimpleNamespace(twitter_report=lambda posts: "twitter"),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    application = SimpleNamespace(bot=bot)

    await _send_twitter_posts(application, service)

    assert bot.send_message.call_count == 1
    assert repository.saved[0]["key"] == "telegram:twitter-post:p1"


@pytest.mark.asyncio
async def test_twitter_posts_do_not_mark_delivered_after_send_failure() -> None:
    post = SimpleNamespace(post_id="p1", author="mgm", published_at=datetime.now(timezone.utc))
    repository = _FakeRepository()
    service = SimpleNamespace(
        settings=SimpleNamespace(telegram_channel_id="@ankarapm"),
        repository=repository,
        twitter=SimpleNamespace(get_twitter_snapshots=AsyncMock(return_value=[post])),
        renderer=SimpleNamespace(twitter_report=lambda posts: "twitter"),
    )
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError("slow")))
    application = SimpleNamespace(bot=bot)

    await _send_twitter_posts(application, service)

    assert bot.send_message.call_count == 1
    assert repository.saved == []


@pytest.mark.asyncio
async def test_power_outages_do_not_mark_delivered_after_send_failure() -> None:
    outage = SimpleNamespace(
        district="Çubuk",
        start_time=datetime(2026, 5, 24, 12, tzinfo=timezone.utc),
        reason="bakım",
    )
    repository = _FakeRepository()
    service = SimpleNamespace(
        settings=SimpleNamespace(telegram_channel_id="@ankarapm"),
        repository=repository,
        tedas=SimpleNamespace(get_outage_snapshots=AsyncMock(return_value=[outage])),
        renderer=SimpleNamespace(outage_report=lambda outages: "outage"),
    )
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError("slow")))
    application = SimpleNamespace(bot=bot)

    await _send_power_outage_alerts(application, service)

    assert bot.send_message.call_count == 1
    assert repository.saved == []


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
