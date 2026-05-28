from __future__ import annotations

from datetime import date, datetime, timezone

from src.bot.scheduler import _forecast_changed
from src.config import Settings
from src.data_sources.schemas import ActualResult, ForecastAnalysis
from src.reports.telegram_renderer import TelegramReportRenderer


def test_compact_daily_alert_contains_only_actionable_forecast() -> None:
    renderer = TelegramReportRenderer(Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None))
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 27),
        generated_at=datetime.now(timezone.utc),
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=24.0,
        final_tmax_c=24.3,
        main_range_low_c=22.7,
        main_range_high_c=25.9,
        model_spread_c=1.9,
        confidence_score=64,
        confidence_factors={},
        verdict="24.3°C merkezli kontrollü tahmin",
    )

    text = renderer.daily_alert(analysis, market=None)

    assert text.startswith("ANKARA TAHMİN · 2026-05-27")
    assert "Tmax: 24.3°C → 24°C" in text
    assert "Aralık: 22.7°C - 25.9°C · Güven: %64" in text
    assert "Model tahminleri" not in text


def test_forecast_change_triggers_on_rounding_or_threshold() -> None:
    base = {"final_tmax_c": 24.3, "rounded_tmax_c": 24}

    assert not _forecast_changed(base, {"final_tmax_c": 24.7, "rounded_tmax_c": 24}, 0.5)
    assert _forecast_changed(base, {"final_tmax_c": 24.8, "rounded_tmax_c": 25}, 0.5)
    assert _forecast_changed(base, {"final_tmax_c": 23.7, "rounded_tmax_c": 24}, 0.5)


def test_market_resolve_alert_is_short() -> None:
    renderer = TelegramReportRenderer(Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None))
    result = ActualResult(
        target_date=date(2026, 5, 27),
        source="Wunderground",
        fetched_at=datetime.now(timezone.utc),
        tmax_c=24.0,
        rounded_tmax_c=24,
    )

    text = renderer.market_resolve_alert(result)

    assert text == "ANKARA MARKET RESOLVE · 2026-05-27\nFinal: 24.0°C → 24°C\nKaynak: Wunderground"
