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
    RadarMotionSignal,
)
from src.forecast.confidence import calculate_confidence
from src.forecast.engine import _fair_probabilities, _risks
from src.forecast.ensemble import calculate_model_weights, ensemble_sigma, probability_sigma, weighted_model_tmax
from src.forecast.local_effects import (
    calculate_airport_heat_island_adjustment,
    calculate_metar_anomaly_adjustment,
    calculate_radar_motion_adjustment,
    calculate_runway_radiation_adjustment,
    calculate_satellite_cloud_cooling_adjustment,
)


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


def test_radar_motion_adjustment_cools_approaching_cells() -> None:
    radar = RadarMotionSignal(
        fetch_timestamp=datetime.now(timezone.utc),
        frame_time=datetime(2026, 5, 24, 9, tzinfo=timezone.utc),
        center_intensity=1.0,
        upwind_intensity=12.0,
        downwind_intensity=0.0,
        max_nearby_intensity=12.0,
        motion="approaching",
        confidence=0.8,
    )

    adjustment = calculate_radar_motion_adjustment(radar, date(2026, 5, 24), "UTC")

    assert adjustment.value_c == -0.45
    assert adjustment.inputs["motion"] == "approaching"
    assert "yaklaşıyor" in adjustment.summary


def test_satellite_cloud_cooling_detects_growing_midday_cloud_and_low_radiation() -> None:
    forecast = ModelForecast(
        model="test",
        available=True,
        target_date=date(2026, 5, 24),
        tmax_c=20.0,
        hourly=[
            ModelHourlyPoint(time=datetime(2026, 5, 24, 9, tzinfo=timezone.utc), cloud_cover_pct=35, shortwave_radiation_wm2=650),
            ModelHourlyPoint(time=datetime(2026, 5, 24, 12, tzinfo=timezone.utc), cloud_cover_pct=90, cloud_cover_low_pct=80, shortwave_radiation_wm2=420),
            ModelHourlyPoint(time=datetime(2026, 5, 24, 13, tzinfo=timezone.utc), cloud_cover_pct=85, cloud_cover_mid_pct=75, shortwave_radiation_wm2=450),
        ],
    )

    adjustment = calculate_satellite_cloud_cooling_adjustment([forecast])

    assert adjustment.value_c < -0.4
    assert adjustment.inputs["cloud_growth_pp"] > 25
    assert "bulut artışı" in adjustment.summary


def test_metar_anomaly_flags_large_model_departure_without_temperature_offset() -> None:
    observed = datetime.now(timezone.utc)
    metar = METARNormalized(
        fetch_timestamp=observed,
        observation_time=observed,
        temperature_c=29,
        dew_point_c=7,
        relative_humidity=24,
        wind_speed_kt=5,
        raw_text="METAR LTAC TEST",
    )
    forecast = ModelForecast(
        model="test",
        available=True,
        target_date=observed.date(),
        tmax_c=22.0,
        hourly=[
            ModelHourlyPoint(time=observed.replace(minute=0, second=0, microsecond=0), temperature_2m_c=22.0),
        ],
    )

    adjustment = calculate_metar_anomaly_adjustment(metar, [forecast], observed.date(), "UTC")

    assert adjustment.value_c == 0.0
    assert adjustment.inputs["severity"] == 0.9
    assert "ayrışma" in adjustment.summary


def test_airport_heat_island_and_runway_radiation_raise_clear_calm_surface() -> None:
    forecast = ModelForecast(
        model="test",
        available=True,
        target_date=date(2026, 5, 24),
        tmax_c=24.0,
        hourly=[
            ModelHourlyPoint(
                time=datetime(2026, 5, 24, 12, tzinfo=timezone.utc),
                cloud_cover_low_pct=10,
                shortwave_radiation_wm2=790,
                wind_speed_10m_kt=4,
                relative_humidity_pct=25,
            )
        ],
    )

    heat = calculate_airport_heat_island_adjustment(None, [forecast])
    runway = calculate_runway_radiation_adjustment(None, [forecast])

    assert heat.value_c == 0.3
    assert runway.value_c == 0.33
