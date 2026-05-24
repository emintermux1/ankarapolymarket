from __future__ import annotations

from datetime import date, datetime, timezone

from src.config import Settings
from src.data_sources.schemas import (
    ForecastAdjustment,
    ForecastAnalysis,
    MarketOutcome,
    MarketSnapshot,
    ModelBundle,
    ModelForecast,
    ModelHourlyPoint,
)
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
    assert "• Polymarket link" in text
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

    assert "Sınır riski: YÜKSEK" in text
    assert "• 20°C: 2.1¢, fair 16.1%, edge +14.0 pp" in text
    assert "• Önerilen bracket: BET YOK" in text
    assert "• En iyi aday (işlem yok): 20°C" in text
    assert "• Beklenen EV: gösterilmiyor (SKIP)" in text
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
    assert text.startswith("ANKARA ESENBOĞA ÖĞLE GÜNCELLEMESİ")
    assert "Canlı sapma: METAR hedef gün değil" in text
    assert "Basınç/üst seviye: 06-09→12-15 basınç trendi -2.0 hPa" in text
    assert "mikroklima" not in text.lower()
    assert "placeholder" not in text


def test_renderer_surfaces_upper_air_profile_signal() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None)
    renderer = TelegramReportRenderer(settings)
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 24),
        generated_at=datetime.now(timezone.utc),
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=22.0,
        final_tmax_c=21.8,
        main_range_low_c=21.0,
        main_range_high_c=22.6,
        model_spread_c=0.7,
        confidence_score=72,
        confidence_factors={},
        verdict="21.8°C merkezli kontrollü tahmin",
        adjustments=[
            ForecastAdjustment(
                name="upper_air_profile",
                value_c=-0.2,
                summary="sabah inversiyon +3.0°C, 500 hPa 5720 m, 250 hPa jet 80 kt",
                inputs={},
            ),
        ],
    )

    text = renderer.daily_report(
        analysis=analysis,
        metar=None,
        taf=None,
        model_bundle=None,
        market=None,
    )

    assert "Üst seviye/profil: sabah inversiyon +3.0°C" in text


def test_advanced_signals_report_lists_professional_layers() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None)
    renderer = TelegramReportRenderer(settings)
    bundle = ModelBundle(
        fetch_timestamp=datetime.now(timezone.utc),
        target_date=date(2026, 5, 24),
        forecasts=[
            ModelForecast(
                model="icon_eu",
                available=True,
                target_date=date(2026, 5, 24),
                tmax_c=22.0,
                hourly=[
                    ModelHourlyPoint(
                        time=datetime(2026, 5, 24, 7, tzinfo=timezone.utc),
                        temperature_2m_c=10.0,
                        temperature_925hpa_c=13.0,
                    ),
                    ModelHourlyPoint(
                        time=datetime(2026, 5, 24, 12, tzinfo=timezone.utc),
                        temperature_925hpa_c=17.0,
                        temperature_850hpa_c=11.0,
                        geopotential_height_500hpa_m=5710,
                        wind_speed_250hpa_kt=88,
                        relative_humidity_700hpa_pct=72,
                        cape_jkg=350,
                        soil_moisture_0_to_1cm_m3m3=0.19,
                        soil_temperature_0cm_c=23.0,
                    ),
                ],
            )
        ],
    )

    text = renderer.advanced_signals_report(bundle)

    assert text.startswith("İLERİ METEOROLOJİ SİNYALLERİ")
    assert "500 hPa yükseklik: 5,710 m" in text
    assert "Jet akımı: 88 kt" in text
    assert "Okyanus/SST/ENSO" in text
