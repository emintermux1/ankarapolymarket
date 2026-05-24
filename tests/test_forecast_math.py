from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from src.data_sources.schemas import (
    EnsembleForecast,
    ForecastAdjustment,
    METARNormalized,
    MarketOutcome,
    MarketSnapshot,
    ModelForecast,
    ModelHourlyPoint,
)
from src.forecast.ai_effect_analysis import calculate_ai_effect_analysis
from src.forecast.confidence import calculate_confidence
from src.forecast.engine import _fair_probabilities, _risks
from src.forecast.ensemble import calculate_model_weights, ensemble_sigma, probability_sigma, weighted_model_tmax
from src.forecast.synoptic_pressure import calculate_synoptic_pressure_adjustment


def test_metar_rejects_dewpoint_above_temperature() -> None:
    with pytest.raises(ValidationError):
        METARNormalized(
            fetch_timestamp=datetime.now(timezone.utc),
            observation_time=datetime.now(timezone.utc),
            temperature_c=10,
            dew_point_c=12,
            wind_speed_kt=3,
            raw_text="METAR LTAC TEST",
        )


def test_equal_weights_when_no_history() -> None:
    forecasts = [
        ModelForecast(model="ecmwf_ifs025", available=True, target_date=date(2026, 5, 24), tmax_c=18.0),
        ModelForecast(model="gfs_seamless", available=True, target_date=date(2026, 5, 24), tmax_c=20.0),
    ]
    weights = calculate_model_weights(forecasts, {})
    assert weights == {"ecmwf_ifs025": 0.5, "gfs_seamless": 0.5}
    assert weighted_model_tmax(forecasts, weights, {"ecmwf_ifs025": 0.0, "gfs_seamless": 1.0}) == 18.5


def test_icon_family_does_not_double_count_when_unweighted() -> None:
    forecasts = [
        ModelForecast(model="icon_eu", available=True, target_date=date(2026, 5, 24), tmax_c=18.0),
        ModelForecast(model="icon_global", available=True, target_date=date(2026, 5, 24), tmax_c=19.0),
        ModelForecast(model="ecmwf_ifs025", available=True, target_date=date(2026, 5, 24), tmax_c=20.0),
        ModelForecast(model="gfs_seamless", available=True, target_date=date(2026, 5, 24), tmax_c=21.0),
    ]

    weights = calculate_model_weights(forecasts, {})

    assert weights["icon_eu"] == pytest.approx(1 / 6)
    assert weights["icon_global"] == pytest.approx(1 / 6)
    assert weights["ecmwf_ifs025"] == pytest.approx(1 / 3)
    assert weights["gfs_seamless"] == pytest.approx(1 / 3)


def test_ensemble_sigma_uses_member_p10_p90_range() -> None:
    ensembles = [
        EnsembleForecast(
            model="icon_eu",
            target_date=date(2026, 5, 24),
            member_tmax_c=[18, 19, 20, 21, 22, 23, 24, 25, 26, 27],
        )
    ]

    sigma = ensemble_sigma(ensembles)

    assert sigma == pytest.approx((26 - 19) / 2.56)
    assert probability_sigma(
        deterministic_spread_c=0.5,
        ensemble_sigma_c=sigma,
        historical_sigma_c=None,
    ) == pytest.approx(sigma)


def test_confidence_penalizes_large_spread() -> None:
    high, _ = calculate_confidence(
        model_spread_c=0.4,
        available_models=3,
        expected_models=3,
        metar=None,
        live_delta_c=None,
        cloud_uncertainty_pct=None,
        precip_spread_mm=None,
        has_taf=True,
        has_history=False,
        market=None,
    )
    low, _ = calculate_confidence(
        model_spread_c=3.2,
        available_models=3,
        expected_models=3,
        metar=None,
        live_delta_c=None,
        cloud_uncertainty_pct=None,
        precip_spread_mm=None,
        has_taf=True,
        has_history=False,
        market=None,
    )
    assert high > low


def test_market_fair_probabilities_use_integer_brackets() -> None:
    market = MarketSnapshot(
        fetch_timestamp=datetime.now(timezone.utc),
        event_id="1",
        title="Highest temperature in Ankara on May 24?",
        slug="test",
        active=True,
        closed=False,
        valid_for_target=True,
        link="https://polymarket.com/event/test",
        outcomes=[
            MarketOutcome(question="Will the highest temperature in Ankara be 18°C on May 24?", bracket="18°C"),
            MarketOutcome(question="Will the highest temperature in Ankara be 19°C on May 24?", bracket="19°C"),
            MarketOutcome(question="Will the highest temperature in Ankara be 23°C or higher on May 24?", bracket="23°C or higher"),
        ],
    )
    probabilities = _fair_probabilities(18.2, 0.8, market)
    assert probabilities["18°C"] > probabilities["19°C"]
    assert probabilities["23°C or higher"] < 0.01


def test_synoptic_pressure_adjustment_uses_pressure_trend_and_upper_air() -> None:
    forecast = ModelForecast(
        model="ecmwf_ifs025",
        available=True,
        target_date=date(2026, 5, 24),
        tmax_c=24.0,
        hourly=[
            ModelHourlyPoint(time=datetime(2026, 5, 24, 7, tzinfo=timezone.utc), pressure_msl_hpa=1012.0),
            ModelHourlyPoint(
                time=datetime(2026, 5, 24, 13, tzinfo=timezone.utc),
                pressure_msl_hpa=1008.0,
                temperature_850hpa_c=8.5,
                geopotential_height_500hpa_m=5685.0,
                cape_jkg=850.0,
            ),
        ],
    )

    adjustment = calculate_synoptic_pressure_adjustment([forecast])

    assert adjustment.name == "synoptic_pressure"
    assert adjustment.value_c == pytest.approx(-0.8)
    assert adjustment.inputs["pressure_trend_hpa"] == pytest.approx(-4.0)
    assert "basınç trendi -4.0 hPa" in adjustment.summary
    assert "500 hPa 5685 m" in adjustment.summary


def test_risks_do_not_invent_generic_weather_when_signals_are_neutral() -> None:
    risks = _risks(
        [
            ForecastAdjustment(name="live_observation", value_c=0.0, summary="METAR hedef gün değil", inputs={}),
            ForecastAdjustment(name="advection", value_c=0.0, summary="ortalama akış 120°", inputs={}),
            ForecastAdjustment(name="cloud_radiation", value_c=0.0, summary="bulut/radyasyon verisi yok", inputs={}),
            ForecastAdjustment(name="rain_soil", value_c=0.0, summary="yağış verisi yok veya düşük", inputs={}),
        ],
        spread=0.7,
        taf=None,
    )

    assert risks["upward"] == "Belirgin yukarı risk sinyali yok"
    assert risks["downward"] == "Belirgin aşağı risk sinyali yok"
    assert risks["critical"] == "Belirgin kritik belirsizlik sinyali yok"
    assert "Bulut kırılması" not in risks["upward"]


def test_ai_effect_analysis_explains_cape_cin_and_ne_wind() -> None:
    metar = METARNormalized(
        fetch_timestamp=datetime.now(timezone.utc),
        observation_time=datetime.now(timezone.utc),
        temperature_c=21,
        dew_point_c=8,
        wind_direction_deg=20,
        wind_speed_kt=12,
        raw_text="METAR LTAC 02012KT",
    )
    forecast = ModelForecast(
        model="gfs_seamless",
        available=True,
        target_date=date(2026, 5, 24),
        hourly=[
            ModelHourlyPoint(
                time=datetime(2026, 5, 24, 15, tzinfo=timezone.utc),
                cape_jkg=850,
                convective_inhibition_jkg=125,
                wind_direction_10m_deg=20,
                wind_speed_10m_kt=12,
            )
        ],
        tmax_c=22,
    )

    adjustment = calculate_ai_effect_analysis(metar, [forecast])

    assert adjustment.value_c == 0.0
    assert adjustment.inputs["bullets"] == [
        "CAPE: 850 J/kg → Öğleden sonra lokal konveksiyon riski var. Ani bulutlanma sıcaklığı baskılayabilir.",
        "CIN: Güçlü → Atmosfer şu an patlamayı baskılıyor. Fırtına oluşumu zor.",
        "Rüzgâr: 020° / 12 KT → Kuzeydoğulu akış hafif serinletici etki yaratıyor.",
    ]


def test_risks_include_synoptic_pressure_signal() -> None:
    risks = _risks(
        [
            ForecastAdjustment(
                name="synoptic_pressure",
                value_c=-0.8,
                summary="06-09→12-15 basınç trendi -4.0 hPa, 850 hPa 8.5°C, 500 hPa 5685 m, CAPE 850 J/kg",
                inputs={"pressure_trend_hpa": -4.0, "midday_cape_max_jkg": 850.0},
            ),
        ],
        spread=0.7,
        taf=None,
    )

    assert "düşen basınç/serin üst seviye" in risks["downward"]
    assert "düşen basınç + CAPE konveksiyon riski" in risks["critical"]
