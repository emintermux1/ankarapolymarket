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
    assert "mikroklima" not in text.lower()
    assert "placeholder" not in text


def test_renderer_adds_cloud_dynamics_panel() -> None:
    settings = Settings(
        TELEGRAM_ADMIN_IDS="",
        TELEGRAM_BOT_TOKEN=None,
        SATELLITE_MOTION_URL="https://example.com/satellite.gif",
        RADAR_MOTION_URL="https://example.com/radar.gif",
    )
    renderer = TelegramReportRenderer(settings)
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 24),
        generated_at=datetime.now(timezone.utc),
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=25.0,
        final_tmax_c=25.4,
        main_range_low_c=24.5,
        main_range_high_c=26.0,
        model_spread_c=0.4,
        confidence_score=72,
        confidence_factors={},
        verdict="25.4°C merkezli kontrollü tahmin",
        adjustments=[
            ForecastAdjustment(
                name="cloud_radiation",
                value_c=0.2,
                summary="10-14 alçak bulut %20, yüksek bulut %60",
                inputs={"low_cloud_mean_pct": 20, "high_cloud_mean_pct": 60, "shortwave_max_wm2": 780},
            ),
            ForecastAdjustment(
                name="ltac_microclimate",
                value_c=0.4,
                summary="Esenboğa batı rüzgârı + pist asfalt etkisi; LTAC sensör offseti",
                inputs={},
            ),
        ],
    )
    points = [
        ModelHourlyPoint(
            time=datetime(2026, 5, 24, hour, tzinfo=timezone.utc),
            temperature_2m_c=22.0 + (hour - 10) * 0.4,
            precipitation_mm=0.20 if hour < 15 else 0.02,
            cloud_cover_pct=70 - (hour - 10) * 5,
            wind_direction_10m_deg=270,
            shortwave_radiation_wm2=700 + (hour - 10) * 10,
        )
        for hour in range(10, 19)
    ]
    bundle = ModelBundle(
        fetch_timestamp=datetime.now(timezone.utc),
        target_date=date(2026, 5, 24),
        forecasts=[ModelForecast(model="gfs_seamless", available=True, target_date=date(2026, 5, 24), hourly=points, tmax_c=25.0)],
    )

    text = renderer.daily_report(
        analysis=analysis,
        metar=None,
        taf=None,
        model_bundle=bundle,
        market=None,
        recent_observations=[{"tmpc": "21.0"}, {"tmpc": "23.2"}],
    )

    assert "Bulut dinamiği+:" in text
    assert "Canlı uydu GIF/link: https://example.com/satellite.gif" in text
    assert "Radar motion ASCII mini map:" in text
    assert "Heatmap 10-18:" in text
    assert "Pist sıcaklık grafiği:" in text
    assert "Son 6 saat trend: 21.0→23.2°C" in text
    assert "Bulut yoğunluğu:" in text
    assert "Güneşlenme:" in text
