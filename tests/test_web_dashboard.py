from __future__ import annotations

import json
from datetime import date, datetime, timezone

from src.config import Settings
from src.data_sources.schemas import ForecastAnalysis, MarketOutcome, MarketSnapshot, ModelBundle, ModelForecast
from src.service import ForecastContext
from src.web.app import dashboard_payload, resource_catalog


def test_dashboard_payload_redacts_secret_values() -> None:
    settings = Settings(
        TELEGRAM_ADMIN_IDS="",
        OPENWEATHER_API_KEY="dummy-openweather-token",
        MAPTILER_API_KEY="dummy-maptiler-token",
        XWEATHER_CLIENT_ID="client-id",
        XWEATHER_CLIENT_SECRET="dummy-xweather-token",
    )
    analysis = ForecastAnalysis(
        target_date=date(2026, 5, 24),
        generated_at=datetime.now(timezone.utc),
        report_timezone="Europe/Istanbul",
        weighted_model_tmax_c=22.1,
        final_tmax_c=22.4,
        main_range_low_c=21.5,
        main_range_high_c=23.3,
        model_spread_c=1.2,
        confidence_score=68,
        confidence_factors={},
        verdict="22.4°C merkezli kontrollü tahmin",
        fair_probabilities={"22°C": 0.31},
        edge_summary="En iyi edge: 22°C +9.0pp; bot fair %31.0, piyasa %22.0",
    )
    bundle = ModelBundle(
        fetch_timestamp=datetime.now(timezone.utc),
        target_date=date(2026, 5, 24),
        forecasts=[ModelForecast(model="openweather", available=True, target_date=date(2026, 5, 24), tmax_c=22.9)],
    )
    market = MarketSnapshot(
        fetch_timestamp=datetime.now(timezone.utc),
        event_id="1",
        title="Highest temperature in Ankara on May 24?",
        slug="highest-temperature-in-ankara-on-may-24-2026",
        target_date=date(2026, 5, 24),
        active=True,
        closed=False,
        valid_for_target=True,
        link="https://polymarket.com/event/highest-temperature-in-ankara-on-may-24-2026",
        outcomes=[MarketOutcome(question="Will Ankara be 22°C?", bracket="22°C", yes_price=0.22)],
    )
    ctx = ForecastContext(analysis=analysis, metar=None, taf=None, model_bundle=bundle, market=market)

    payload = dashboard_payload(ctx, settings)
    serialized = json.dumps(payload)

    assert "dummy-openweather-token" not in serialized
    assert "dummy-maptiler-token" not in serialized
    assert "dummy-xweather-token" not in serialized
    assert any(item["env"] == "OPENWEATHER_API_KEY" and item["configured"] for item in payload["resources"])
    assert payload["market"]["outcomes"][0]["edgePp"] == 9.0


def test_resource_catalog_marks_xweather_pair_configured_only_with_secret() -> None:
    missing_secret = Settings(TELEGRAM_ADMIN_IDS="", XWEATHER_CLIENT_ID="client-id")
    configured = Settings(TELEGRAM_ADMIN_IDS="", XWEATHER_CLIENT_ID="client-id", XWEATHER_CLIENT_SECRET="secret")

    assert next(item for item in resource_catalog(missing_secret) if item["name"] == "XWeather")["configured"] is False
    assert next(item for item in resource_catalog(configured) if item["name"] == "XWeather")["configured"] is True
