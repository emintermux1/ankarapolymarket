from __future__ import annotations

from datetime import date, datetime, timezone

from src.config import Settings
from src.data_sources.schemas import ForecastAdjustment, ForecastAnalysis, MarketOutcome, MarketSnapshot, ModelBundle, ModelForecast, ModelHourlyPoint
from src.reports.telegram_renderer import TelegramReportRenderer


def test_renderer_marks_missing_market_without_fake_numbers() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None)
    renderer = TelegramReportRenderer(settings)
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 24),
        generated_at=datetime.now(timezone.utc),
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=None,
        final_tmax_c=None,
        main_range_low_c=None,
        main_range_high_c=None,
        model_spread_c=None,
        confidence_score=0,
        confidence_factors={},
        verdict="Veri eksik; tahmin üretilemedi",
    )
    text = renderer.daily_report(analysis=analysis, metar=None, taf=None, model_bundle=None, market=None)
    assert "ilgili market bulunamadı" in text
    assert "veri yok" in text
    assert "unavailable" not in text
    assert "Yatırım tavsiyesi değildir" in text
    assert "Polymarket marketi" in text
    assert "* Polymarket link" not in text


def test_renderer_does_not_show_ev_for_skipped_boundary_bet() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None)
    renderer = TelegramReportRenderer(settings)
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 23),
        generated_at=datetime.now(timezone.utc),
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=19.0,
        final_tmax_c=19.2,
        main_range_low_c=16.9,
        main_range_high_c=21.5,
        model_spread_c=6.1,
        probability_sigma_c=1.8,
        confidence_score=46,
        confidence_factors={},
        verdict="19.2°C merkezli, belirsizlik yüksek",
        fair_probabilities={"20°C": 0.161},
        edge_summary="En iyi edge: 20°C +14.0pp; bot fair %16.1, piyasa %2.1",
    )
    market = MarketSnapshot(
        fetch_timestamp=datetime.now(timezone.utc),
        event_id="1",
        title="Highest temperature in Ankara on May 23?",
        slug="highest-temperature-in-ankara-on-may-23-2026",
        target_date=date(2026, 5, 23),
        active=True,
        closed=False,
        valid_for_target=True,
        link="https://polymarket.com/event/highest-temperature-in-ankara-on-may-23-2026",
        volume=44120,
        liquidity=21886,
        outcomes=[
            MarketOutcome(
                question="Will the highest temperature in Ankara be 20°C on May 23?",
                bracket="20°C",
                yes_price=0.021,
            ),
        ],
    )

    text = renderer.daily_report(analysis=analysis, metar=None, taf=None, model_bundle=None, market=market)

    assert "⚠️ Risk 🔴 YÜKSEK" in text
    assert "💵 Polymarket Canlı Fiyat (20°C): 2.1¢" in text
    assert "En güçlü fiyat/fair ayrışması 20°C için +14.0 pp" in text
    assert "• Beklenen EV: $" not in text


def test_renderer_uses_report_labels_and_hides_placeholder_adjustments() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None)
    renderer = TelegramReportRenderer(settings)
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 24),
        generated_at=datetime.now(timezone.utc),
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=18.0,
        final_tmax_c=17.5,
        main_range_low_c=17.0,
        main_range_high_c=18.0,
        model_spread_c=0.8,
        confidence_score=72,
        confidence_factors={},
        verdict="17.5°C merkezli kontrollü tahmin",
        adjustments=[
            ForecastAdjustment(name="live_observation", value_c=0.0, summary="METAR hedef gün değil", inputs={}),
            ForecastAdjustment(name="synoptic_pressure", value_c=-0.2, summary="06-09→12-15 basınç trendi -2.0 hPa", inputs={}),
            ForecastAdjustment(name="ltac_microclimate", value_c=0.0, summary="placeholder", inputs={}),
        ],
    )
    text = renderer.daily_report(
        analysis=analysis,
        metar=None,
        taf=None,
        model_bundle=None,
        market=None,
        report_label="12:00",
    )
    assert text.startswith("☁️ Ankara Esenboğa Günün Tahmini")
    assert "Meteorolojik Veriler" in text
    assert "Basınç: veri yok" in text
    assert "mikroklima" not in text.lower()
    assert "placeholder" not in text


def test_renderer_highlights_temperature_forecast_trends() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None)
    renderer = TelegramReportRenderer(settings)
    now = datetime(2026, 5, 24, 9, 0, tzinfo=timezone.utc)
    previous = ForecastAnalysis(
        target_date=date(2026, 5, 24),
        generated_at=now,
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=20.2,
        final_tmax_c=20.4,
        main_range_low_c=19.5,
        main_range_high_c=21.2,
        model_spread_c=1.0,
        confidence_score=65,
        confidence_factors={},
        verdict="20.4°C merkezli kontrollü tahmin",
    )
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 24),
        generated_at=now,
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=20.8,
        final_tmax_c=21.0,
        main_range_low_c=20.0,
        main_range_high_c=22.0,
        model_spread_c=3.0,
        confidence_score=66,
        confidence_factors={},
        verdict="21.0°C merkezli kontrollü tahmin",
    )
    bundle = ModelBundle(
        fetch_timestamp=now,
        target_date=date(2026, 5, 24),
        forecasts=[
            ModelForecast(model="ecmwf", available=True, target_date=date(2026, 5, 24), tmax_c=22.0),
            ModelForecast(model="gfs", available=True, target_date=date(2026, 5, 24), tmax_c=19.0),
            ModelForecast(model="icon", available=True, target_date=date(2026, 5, 24), tmax_c=20.0),
        ],
    )

    text = renderer.daily_report(
        analysis=analysis,
        metar=None,
        taf=None,
        model_bundle=bundle,
        market=None,
        previous_analysis=previous,
        previous_model_tmax_c={"ecmwf": 21.0, "gfs": 19.8, "icon": 20.0},
    )

    assert "👥 Bot Tahmini: 21.0°C 🔺 +0.6°C" in text
    assert 'ECMWF</a>: 22.0°C 🔺 +1.0°C' in text
    assert 'GFS</a>: 19.0°C 🔻 -0.8°C' in text
    assert 'ICON</a>: 20.0°C\n' in text


def test_renderer_outputs_rich_weather_template_with_model_links() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None)
    renderer = TelegramReportRenderer(settings)
    now = datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc)
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 23),
        generated_at=now,
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=18.3,
        final_tmax_c=19.0,
        main_range_low_c=17.1,
        main_range_high_c=19.7,
        model_spread_c=3.1,
        probability_sigma_c=1.1,
        confidence_score=58,
        confidence_factors={},
        verdict="19.0°C merkezli kontrollü tahmin",
        adjustments=[
            ForecastAdjustment(
                name="advection",
                value_c=0.2,
                summary="850 hPa hafif sıcak adveksiyon",
                inputs={},
            ),
            ForecastAdjustment(
                name="synoptic_pressure",
                value_c=-0.2,
                summary="06-09→12-15 basınç trendi -2.0 hPa, CAPE 650 J/kg",
                inputs={"pressure_trend_hpa": -2.0},
            ),
        ],
        fair_probabilities={"19°C": 0.42},
    )
    market = MarketSnapshot(
        fetch_timestamp=now,
        event_id="1",
        title="Highest temperature in Ankara on May 23?",
        slug="highest-temperature-in-ankara-on-may-23-2026",
        target_date=date(2026, 5, 23),
        active=True,
        closed=False,
        valid_for_target=True,
        link="https://polymarket.com/event/highest-temperature-in-ankara-on-may-23-2026",
        volume=24236,
        outcomes=[
            MarketOutcome(
                question="Will the highest temperature in Ankara be 19°C on May 23?",
                bracket="19°C",
                yes_price=0.24,
            ),
        ],
    )
    bundle = ModelBundle(
        fetch_timestamp=now,
        target_date=date(2026, 5, 23),
        forecasts=[
            ModelForecast(
                model="icon_eu",
                available=True,
                target_date=date(2026, 5, 23),
                tmax_c=16.6,
                hourly=[
                    ModelHourlyPoint(time=datetime(2026, 5, 23, 9, tzinfo=timezone.utc), temperature_2m_c=15.4, cloud_cover_pct=40, precipitation_mm=0.0),
                    ModelHourlyPoint(time=datetime(2026, 5, 23, 11, tzinfo=timezone.utc), temperature_2m_c=16.0, cloud_cover_pct=35, precipitation_mm=0.0, cape_jkg=500),
                    ModelHourlyPoint(time=datetime(2026, 5, 23, 13, tzinfo=timezone.utc), temperature_2m_c=17.0, cloud_cover_pct=45, precipitation_mm=0.1, pressure_msl_hpa=1014),
                    ModelHourlyPoint(time=datetime(2026, 5, 23, 15, tzinfo=timezone.utc), temperature_2m_c=19.0, cloud_cover_pct=70, precipitation_mm=0.2, temperature_850hpa_c=12),
                ],
            ),
            ModelForecast(model="gfs_seamless", available=True, target_date=date(2026, 5, 23), tmax_c=19.6),
        ],
    )

    text = renderer.daily_report(
        analysis=analysis,
        metar=None,
        taf=None,
        model_bundle=bundle,
        market=market,
        temperature_momentum=(2.1, 90),
    )

    assert "Ankara Esenboğa Günün Tahmini" in text
    assert '<a href="https://polymarket.com/event/highest-temperature-in-ankara-on-may-23-2026">Highest temperature in Ankara on May 23?</a> · Vol: $24,236' in text
    assert "🕒 Saatlik Beklentiler" in text
    assert "│  +2.1°C artış" in text
    assert '<a href="https://www.dwd.de/EN/ourservices/nwp_forecast_data/nwp_forecast_data.html">ICON-EU</a>: 16.6°C' in text
    assert "👉 Meteorolojik Veriler" in text
    assert "Bulut Aktiviteleri:" in text
