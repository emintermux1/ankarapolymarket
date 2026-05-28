from __future__ import annotations

from datetime import date, datetime, timezone

from src.config import Settings
from src.data_sources.schemas import (
    ActualResult,
    ForecastAdjustment,
    ForecastAnalysis,
    ForumAnalysis,
    ForumPost,
    MarketOutcome,
    MarketSnapshot,
    METARNormalized,
    ModelBundle,
    ModelForecast,
    ModelHourlyPoint,
    TAFForecastPeriod,
    TAFNormalized,
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


def test_hourly_max_forecast_is_compact_and_max_focused() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None)
    renderer = TelegramReportRenderer(settings)
    generated_at = datetime(2026, 5, 24, 9, 0, tzinfo=timezone.utc)
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 24),
        generated_at=generated_at,
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=24.2,
        final_tmax_c=24.5,
        main_range_low_c=23.6,
        main_range_high_c=25.4,
        model_spread_c=1.0,
        probability_sigma_c=0.9,
        confidence_score=82,
        confidence_factors={},
        verdict="24.5°C merkezli kontrollü tahmin",
        fair_probabilities={"25°C": 0.367},
    )
    metar = METARNormalized(
        fetch_timestamp=generated_at,
        observation_time=generated_at,
        temperature_c=23.0,
        dew_point_c=10.0,
        relative_humidity=44,
        wind_direction_deg=120,
        wind_speed_kt=5.0,
        pressure_hpa=1019.0,
        visibility_m=9999,
        raw_text="LTAC 240900Z 12005KT 9999 SCT040 23/10 Q1019",
    )
    bundle = ModelBundle(
        fetch_timestamp=generated_at,
        target_date=date(2026, 5, 24),
        forecasts=[
            ModelForecast(
                model="icon_eu",
                available=True,
                target_date=date(2026, 5, 24),
                tmax_c=24.7,
                hourly=[
                    ModelHourlyPoint(time=datetime(2026, 5, 24, 12, tzinfo=timezone.utc), temperature_2m_c=23.5),
                    ModelHourlyPoint(time=datetime(2026, 5, 24, 13, tzinfo=timezone.utc), temperature_2m_c=24.7),
                ],
            ),
            ModelForecast(model="ecmwf", available=True, target_date=date(2026, 5, 24), tmax_c=24.1),
        ],
    )
    market = MarketSnapshot(
        fetch_timestamp=generated_at,
        event_id="1",
        title="Highest temperature in Ankara on May 24?",
        slug="highest-temperature-in-ankara-on-may-24-2026",
        target_date=date(2026, 5, 24),
        active=True,
        closed=False,
        valid_for_target=True,
        link="https://polymarket.com/event/highest-temperature-in-ankara-on-may-24-2026",
        outcomes=[MarketOutcome(question="Will Ankara be 25°C?", bracket="25°C", yes_price=0.19)],
    )

    text = renderer.hourly_max_forecast(analysis=analysis, metar=metar, taf=None, model_bundle=bundle, market=market)

    assert text.startswith("ANKARA LTAC SAAT BAŞI MAKS TAHMİN")
    assert "Bugünün beklenen en yüksek sıcaklığı: 24.5°C" in text
    assert "Market tescil adayı: 25°C" in text
    assert "Güven: 82/100" in text
    assert "Market fiyatlaması" not in text
    assert len(text.splitlines()) <= 12


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


def test_renderer_includes_ai_effect_analysis_section() -> None:
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
            ForecastAdjustment(
                name="ai_effect_analysis",
                value_c=0.0,
                summary="CAPE 850 J/kg, CIN güçlü, rüzgâr 020° / 12 kt",
                inputs={
                    "bullets": [
                        "CAPE: 850 J/kg → Öğleden sonra lokal konveksiyon riski var. Ani bulutlanma sıcaklığı baskılayabilir.",
                        "CIN: Güçlü → Atmosfer şu an patlamayı baskılıyor. Fırtına oluşumu zor.",
                    ]
                },
            )
        ],
    )

    text = renderer.daily_report(analysis=analysis, metar=None, taf=None, model_bundle=None, market=None)

    assert "AI Etki Analizi:" in text
    assert "• CAPE: 850 J/kg → Öğleden sonra lokal konveksiyon riski var." in text


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

    assert "• Beklenen maksimum: 21.0°C 🔺 +0.6°C" in text
    assert "• ECMWF: 22.0°C 🔺 +1.0°C" in text
    assert "• GFS: 19.0°C 🔻 -0.8°C" in text
    assert "• ICON: 20.0°C\n" in text


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
        forecasts=[
            ModelForecast(
                model="gfs_seamless",
                available=True,
                target_date=date(2026, 5, 24),
                hourly=points,
                tmax_c=25.0,
            )
        ],
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


def test_aviation_report_surfaces_wunderground_integer_settlement_rules() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None)
    renderer = TelegramReportRenderer(settings)
    target = date(2026, 5, 24)
    generated_at = datetime(2026, 5, 24, 10, 30, tzinfo=timezone.utc)
    analysis = ForecastAnalysis(
        target_date=target,
        generated_at=generated_at,
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=20.7,
        final_tmax_c=20.6,
        main_range_low_c=19.8,
        main_range_high_c=21.4,
        model_spread_c=0.8,
        probability_sigma_c=0.8,
        confidence_score=68,
        confidence_factors={},
        verdict="20.6°C merkezli kontrollü tahmin",
        fair_probabilities={"21°C": 0.41},
    )
    metar = METARNormalized(
        fetch_timestamp=generated_at,
        observation_time=datetime(2026, 5, 24, 10, 20, tzinfo=timezone.utc),
        temperature_c=21.0,
        dew_point_c=8.0,
        wind_direction_deg=40,
        wind_speed_kt=8,
        visibility_m=9999,
        cloud_layers=[{"cover": "SCT", "base": 4500}],
        raw_text="LTAC 241020Z 04008KT 9999 SCT045 21/08 Q1018",
    )
    taf = TAFNormalized(
        fetch_timestamp=generated_at,
        issue_time=datetime(2026, 5, 24, 8, tzinfo=timezone.utc),
        valid_from=datetime(2026, 5, 24, 9, tzinfo=timezone.utc),
        valid_to=datetime(2026, 5, 25, 9, tzinfo=timezone.utc),
        raw_text="TAF LTAC TEMPO TSRA CB",
        periods=[
            TAFForecastPeriod(
                time_from=datetime(2026, 5, 24, 11, tzinfo=timezone.utc),
                time_to=datetime(2026, 5, 24, 14, tzinfo=timezone.utc),
                change="TEMPO",
                weather="TSRA",
                clouds=[{"type": "CB", "base": 4000}],
            )
        ],
    )
    bundle = ModelBundle(
        fetch_timestamp=generated_at,
        target_date=target,
        forecasts=[
            ModelForecast(
                model="ecmwf_ifs025",
                available=True,
                target_date=target,
                tmax_c=20.8,
                hourly=[
                    ModelHourlyPoint(
                        time=datetime(2026, 5, 24, 14, tzinfo=timezone.utc),
                        temperature_2m_c=20.8,
                        cloud_cover_pct=55,
                        shortwave_radiation_wm2=690,
                        cape_jkg=480,
                        precipitation_mm=0.3,
                        temperature_850hpa_c=11.2,
                    )
                ],
            )
        ],
    )
    intraday = ActualResult(
        target_date=target,
        source="IEM_ASOS_intraday",
        fetched_at=generated_at,
        tmax_c=21.0,
        rounded_tmax_c=21,
        raw_payload={"observation_count": 12, "max_observation_time": "2026-05-24 13:20"},
    )
    market = MarketSnapshot(
        fetch_timestamp=generated_at,
        event_id="1",
        title="Highest temperature in Ankara on May 24?",
        slug="highest-temperature-in-ankara-on-may-24-2026",
        active=True,
        closed=False,
        valid_for_target=True,
        link="https://polymarket.com/event/highest-temperature-in-ankara-on-may-24-2026",
        outcomes=[MarketOutcome(question="21?", bracket="21°C", yes_price=0.22)],
    )

    text = renderer.aviation_report(
        analysis=analysis,
        metar=metar,
        taf=taf,
        model_bundle=bundle,
        market=market,
        wunderground_result=None,
        intraday_result=intraday,
        wunderground_url="https://www.wunderground.com/history/daily/tr/%C3%A7ubuk/LTAC/date/2026-5-24",
        now=generated_at,
    )

    assert text.startswith("LTAC HAVACILIK + WUNDERGROUND BRİFİNGİ")
    assert "METAR kaynaklı tam °C" in text
    assert "Canlı ASOS/METAR proxy: 21°C" in text
    assert "Son METAR sıcaklığı: 21°C" in text
    assert "TAF konveksiyon/yağış: TEMPO TSRA" in text
    assert "CAPE max 480 J/kg" in text
    assert "Bot settlement adayı: 21°C" in text
    assert "MGM'nin küsuratlı" in text


def test_renderer_adds_rounding_alarm_with_metar_thresholds() -> None:
    settings = Settings(TELEGRAM_ADMIN_IDS="", TELEGRAM_BOT_TOKEN=None)
    renderer = TelegramReportRenderer(settings)
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 24),
        generated_at=datetime.now(timezone.utc),
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=20.0,
        final_tmax_c=20.0,
        main_range_low_c=19.5,
        main_range_high_c=20.5,
        model_spread_c=0.8,
        probability_sigma_c=0.8,
        confidence_score=72,
        confidence_factors={},
        verdict="20.0°C merkezli kontrollü tahmin",
    )
    metar = METARNormalized(
        fetch_timestamp=datetime.now(timezone.utc),
        observation_time=datetime(2026, 5, 24, 9, 50, tzinfo=timezone.utc),
        temperature_c=20.0,
        dew_point_c=8.0,
        wind_direction_deg=20,
        wind_speed_kt=14,
        cloud_layers=[{"cover": "BKN", "base": 4500}],
        raw_text="LTAC 240950Z 02014KT 9999 BKN045 20/08 Q1016 NOSIG",
    )

    text = renderer.daily_report(analysis=analysis, metar=metar, taf=None, model_bundle=None, market=None)

    assert "🚨 YUVARLAMA ALARMI:" in text
    assert "Marketin kaderi 20.5°C sınırında" in text
    assert "20.5°C = 21°C" in text
    assert "• Anlık değer: 20.0°C (68°F)" in text
    assert "69°F (20.55°C) için +0.55°C gerekiyor" in text
    assert "020° / 14 KT -> kuzeyli akış ısınmayı baskılayacak" in text
    assert "BKN (güneşlenme kısıtlı)" in text
    assert "NOSIG (değişim beklenmiyor)" in text
