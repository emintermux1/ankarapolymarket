from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from src.config import Settings, get_settings
from src.data_sources.schemas import (
    ForecastAdjustment,
    ForecastAnalysis,
    ForumAnalysis,
    MarketSnapshot,
    METARNormalized,
    ModelBundle,
    ModelForecast,
    ModelHourlyPoint,
    SourceHealth,
    TAFNormalized,
)
from src.db.repository import Repository, create_repository
from src.service import ForecastContext, ForecastService


STATIC_DIR = Path(__file__).with_name("static")
INDEX_PATH = STATIC_DIR / "index.html"


def create_app(
    settings: Settings | None = None,
    repository: Repository | None = None,
    service: ForecastService | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    repository = repository or create_repository(settings)
    service = service or ForecastService(settings, repository)

    app = FastAPI(title="Ankara LTAC Market Desk", version="0.2.0")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return INDEX_PATH.read_text(encoding="utf-8")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "station": settings.ltac_icao}

    @app.get("/api/dashboard", response_class=JSONResponse)
    async def dashboard(target_date: str | None = Query(default=None, alias="date")) -> dict[str, Any]:
        target = _parse_target_date(target_date, service.default_target_date())
        ctx = await service.build_forecast_context(target_date=target, report_label="web")
        report = service.renderer.daily_report(
            analysis=ctx.analysis,
            metar=ctx.metar,
            taf=ctx.taf,
            model_bundle=ctx.model_bundle,
            market=ctx.market,
            forum=ctx.forum,
            recent_observations=ctx.recent_observations,
            report_label="web",
            previous_analysis=ctx.previous_analysis,
            previous_model_tmax_c=ctx.previous_model_tmax_c,
        )
        return dashboard_payload(ctx, settings, report_text=report)

    @app.get("/api/sources", response_class=JSONResponse)
    async def sources() -> dict[str, Any]:
        health = await service.check_sources()
        return {
            "checkedAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "sources": [_source_health_payload(item) for item in health],
            "resources": resource_catalog(settings, health),
        }

    @app.get("/api/report", response_class=PlainTextResponse)
    async def report(target_date: str | None = Query(default=None, alias="date")) -> str:
        target = _parse_target_date(target_date, service.default_target_date())
        return await service.render_daily_report(target_date=target, report_label="web")

    return app


def dashboard_payload(ctx: ForecastContext, settings: Settings, *, report_text: str | None = None) -> dict[str, Any]:
    return {
        "meta": {
            "targetDate": ctx.analysis.target_date.isoformat(),
            "generatedAt": _iso(ctx.analysis.generated_at),
            "timezone": ctx.analysis.report_timezone,
            "station": {
                "icao": settings.ltac_icao,
                "name": "Ankara Esenboğa",
                "latitude": settings.ltac_latitude,
                "longitude": settings.ltac_longitude,
                "elevationM": settings.ltac_elevation_m,
            },
            "links": {
                "windySatellite": settings.satellite_motion_url,
                "windyRadar": settings.radar_motion_url,
                "polymarket": ctx.market.link if ctx.market else None,
                "havaforum": settings.havaforum_thread_url,
            },
        },
        "summary": _summary_payload(ctx.analysis),
        "weather": {
            "metar": _metar_payload(ctx.metar),
            "taf": _taf_payload(ctx.taf),
            "recentObservations": _recent_observations_payload(ctx.recent_observations or []),
        },
        "models": _models_payload(ctx.model_bundle, ctx.analysis),
        "market": _market_payload(ctx.market, ctx.analysis),
        "adjustments": [_adjustment_payload(item) for item in ctx.analysis.adjustments],
        "risks": ctx.analysis.risks,
        "forum": _forum_payload(ctx.forum),
        "resources": resource_catalog(settings),
        "methodCards": _method_cards(),
        "reportText": report_text,
    }


def resource_catalog(settings: Settings, source_health: list[SourceHealth] | None = None) -> list[dict[str, Any]]:
    health_by_source = {item.source.lower(): item for item in source_health or []}
    definitions = [
        ResourceDef("AviationWeather", "LTAC METAR/TAF", "Airport settlement anchor; city-center sapmasını engeller.", None, "AviationWeather"),
        ResourceDef("Open-Meteo", "ICON/ECMWF/GFS stack", "ICON-EU, ICON-Global, ECMWF ve GFS deterministik + ensemble gövdesi.", None, "Open-Meteo"),
        ResourceDef("Polymarket", "Gamma/CLOB/Data", "Market metadata, order book, spread, implied probability ve son işlemler.", None, "Polymarket"),
        ResourceDef("IEM ASOS", "Observed high proxy", "LTAC canlı/geriye dönük METAR sıcaklık izleme.", None, "IEM_ASOS"),
        ResourceDef("Wunderground", "Settlement check", "Gün sonu Wunderground/airport history çözüm kontrolü.", None, "Wunderground"),
        ResourceDef("HavaForum", "Local nowcast", "Ankara thread sinyalleri: konveksiyon, bulut, yağış, bölgesel akış.", None, "HavaForum"),
        ResourceDef("Visual Crossing", "Keyed forecast/result", "Forecast fallback ve final tmax doğrulama.", "VISUALCROSSING_API_KEY", "VisualCrossing"),
        ResourceDef("Tomorrow.io", "Cloud/radiation forecast", "Bulut tabanı, tavan, solar GHI ve saatlik sıcaklık.", "TOMORROW_API_KEY", "Tomorrow.io"),
        ResourceDef("OpenWeather", "3-hour fallback model", "Paylaşılan OpenWeather key ile ek forecast modeli.", "OPENWEATHER_API_KEY", "OpenWeather"),
        ResourceDef("Weatherbit", "Daily fallback model", "Weatherbit daily max_temp ile model sepetini genişletir.", "WEATHERBIT_API_KEY", "Weatherbit"),
        ResourceDef("CheckWX", "Aviation fallback", "METAR/TAF yedeği; AviationWeather düşerse devreye girer.", "CHECKWX_API_KEY", "CheckWX"),
        ResourceDef("AVWX", "Aviation env-ready", "AVWX anahtarı tanımlıysa sonraki fallback adapter için hazır.", "AVWX_API_KEY", None),
        ResourceDef("Windy", "Radar/satellite motion", "Dashboard linklerinde radar ve uydu hareketi.", "WINDY_API_KEY", None),
        ResourceDef("MapTiler", "Map layer env-ready", "Harita katmanı anahtarı server-side tutulur; UI'ye secret basılmaz.", "MAPTILER_API_KEY", None),
        ResourceDef("Mapbox", "Map layer env-ready", "Alternatif harita katmanı anahtarı server-side tutulur.", "MAPBOX_API_KEY", None),
        ResourceDef("Cesium", "3D terrain env-ready", "İleri harita/terrain görünümü için hazır.", "CESIUM_ION_TOKEN", None),
        ResourceDef("HERE", "Geocoding env-ready", "Konum/rota API kullanımına hazır.", "HERE_API_KEY", None),
        ResourceDef("Meteoblue", "Forecast env-ready", "Meteoblue forecast fallback için env hazır.", "METEOBLUE_API_KEY", None),
        ResourceDef("WeatherAPI", "Forecast env-ready", "WeatherAPI forecast fallback için env hazır.", "WEATHERAPI_API_KEY", None),
        ResourceDef("Stormglass", "Weather env-ready", "Stormglass anahtarı kayıtlı; Ankara için ikincil/opsiyonel.", "STORMGLASS_API_KEY", None),
        ResourceDef("XWeather", "Forecast env-ready", "XWeather id/secret ikilisi için güvenli env slotu.", "XWEATHER_CLIENT_ID", None),
        ResourceDef("Copernicus CDS", "Climate archive env-ready", "Kalibrasyon/backtest için iklim arşivi anahtar slotu.", "COPERNICUS_CDS_API_KEY", None),
        ResourceDef("RapidAPI", "API hub env-ready", "RapidAPI kaynakları için ortak env slotu.", "RAPIDAPI_KEY", None),
        ResourceDef("Rainbow", "Wallet/API env-ready", "Rainbow anahtarı trading/ops entegrasyonları için ayrıldı.", "RAINBOW_API_KEY", None),
        ResourceDef("OpenAI", "AI report summary", "LLM özet/etki analizi için güvenli env slotu.", "OPENAI_API_KEY", None),
    ]
    resources = []
    for item in definitions:
        health = health_by_source.get(item.health_source.lower()) if item.health_source else None
        configured = _env_configured(settings, item.env) if item.env else True
        state = health.state.value if health else ("enabled" if configured else "missing")
        resources.append(
            {
                "name": item.name,
                "label": item.label,
                "role": item.role,
                "env": item.env,
                "configured": configured,
                "state": state,
                "latencyMs": round(health.latency_ms) if health and health.latency_ms is not None else None,
                "message": health.message if health else None,
            }
        )
    return resources


@dataclass(frozen=True)
class ResourceDef:
    name: str
    label: str
    role: str
    env: str | None
    health_source: str | None


def _parse_target_date(value: str | None, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD") from exc


def _summary_payload(analysis: ForecastAnalysis) -> dict[str, Any]:
    return {
        "finalTmaxC": analysis.final_tmax_c,
        "weightedModelTmaxC": analysis.weighted_model_tmax_c,
        "rangeLowC": analysis.main_range_low_c,
        "rangeHighC": analysis.main_range_high_c,
        "modelSpreadC": analysis.model_spread_c,
        "ensembleSigmaC": analysis.ensemble_sigma_c,
        "probabilitySigmaC": analysis.probability_sigma_c,
        "confidenceScore": analysis.confidence_score,
        "confidenceLabel": _confidence_label(analysis.confidence_score),
        "boundaryRisk": _boundary_risk(analysis.final_tmax_c),
        "verdict": analysis.verdict,
        "edgeSummary": analysis.edge_summary,
        "rationale": analysis.rationale_bullets,
        "fairProbabilities": analysis.fair_probabilities,
    }


def _metar_payload(metar: METARNormalized | None) -> dict[str, Any] | None:
    if metar is None:
        return None
    return {
        "source": metar.source,
        "station": metar.station,
        "observedAt": _iso(metar.observation_time),
        "ageMinutes": round(metar.age_minutes),
        "temperatureC": metar.temperature_c,
        "dewPointC": metar.dew_point_c,
        "relativeHumidity": metar.relative_humidity,
        "windDirectionDeg": metar.wind_direction_deg,
        "windSpeedKt": metar.wind_speed_kt,
        "windGustKt": metar.wind_gust_kt,
        "pressureHpa": metar.pressure_hpa,
        "visibilityM": metar.visibility_m,
        "cloudLayers": metar.cloud_layers,
        "rawText": metar.raw_text,
        "stale": metar.is_stale,
    }


def _taf_payload(taf: TAFNormalized | None) -> dict[str, Any] | None:
    if taf is None:
        return None
    return {
        "source": taf.source,
        "station": taf.station,
        "issuedAt": _iso(taf.issue_time),
        "validFrom": _iso(taf.valid_from),
        "validTo": _iso(taf.valid_to),
        "rainOrStormRisk": taf.rain_or_storm_risk,
        "rawText": taf.raw_text,
        "periods": [
            {
                "from": _iso(period.time_from),
                "to": _iso(period.time_to),
                "change": period.change,
                "probability": period.probability,
                "windDirectionDeg": period.wind_direction_deg,
                "windSpeedKt": period.wind_speed_kt,
                "weather": period.weather,
                "clouds": period.clouds,
            }
            for period in taf.periods[:8]
        ],
    }


def _models_payload(bundle: ModelBundle | None, analysis: ForecastAnalysis) -> list[dict[str, Any]]:
    if bundle is None:
        return []
    return [_model_payload(forecast, analysis) for forecast in bundle.forecasts]


def _model_payload(forecast: ModelForecast, analysis: ForecastAnalysis) -> dict[str, Any]:
    peak = _peak_point(forecast.hourly)
    return {
        "model": forecast.model,
        "label": _display_model_name(forecast.model),
        "available": forecast.available,
        "tmaxC": forecast.tmax_c,
        "weight": analysis.model_weights.get(forecast.model),
        "biasOffsetC": analysis.model_bias_offsets.get(forecast.model),
        "unavailableReason": forecast.unavailable_reason,
        "peakTime": _iso(peak.time) if peak else None,
        "hourly": [_hourly_point_payload(point) for point in forecast.hourly[:32]],
    }


def _hourly_point_payload(point: ModelHourlyPoint) -> dict[str, Any]:
    return {
        "time": _iso(point.time),
        "temperatureC": point.temperature_2m_c,
        "humidityPct": point.relative_humidity_pct,
        "precipMm": point.precipitation_mm,
        "cloudPct": point.cloud_cover_pct,
        "windKt": point.wind_speed_10m_kt,
        "windDirDeg": point.wind_direction_10m_deg,
        "radiationWm2": point.shortwave_radiation_wm2,
        "capeJkg": point.cape_jkg,
    }


def _market_payload(market: MarketSnapshot | None, analysis: ForecastAnalysis) -> dict[str, Any] | None:
    if market is None:
        return None
    outcomes = []
    for outcome in market.outcomes:
        implied = outcome.implied_probability
        fair = analysis.fair_probabilities.get(outcome.bracket)
        outcomes.append(
            {
                "question": outcome.question,
                "bracket": outcome.bracket,
                "yesPrice": outcome.yes_price,
                "bestBid": outcome.best_bid,
                "bestAsk": outcome.best_ask,
                "spread": outcome.spread,
                "impliedProbability": implied,
                "fairProbability": fair,
                "edgePp": round((fair - implied) * 100, 1) if fair is not None and implied is not None else None,
                "liquidity": outcome.liquidity,
                "volume": outcome.volume,
            }
        )
    outcomes.sort(key=lambda item: item["edgePp"] if item["edgePp"] is not None else -999, reverse=True)
    return {
        "title": market.title,
        "slug": market.slug,
        "targetDate": market.target_date.isoformat() if market.target_date else None,
        "active": market.active,
        "closed": market.closed,
        "validForTarget": market.valid_for_target,
        "validationMessage": market.validation_message,
        "link": market.link,
        "resolutionSource": market.resolution_source,
        "liquidity": market.liquidity,
        "volume": market.volume,
        "outcomes": outcomes,
    }


def _adjustment_payload(item: ForecastAdjustment) -> dict[str, Any]:
    return {
        "name": item.name,
        "label": _adjustment_label(item.name),
        "valueC": item.value_c,
        "summary": item.summary,
    }


def _forum_payload(forum: ForumAnalysis | None) -> dict[str, Any] | None:
    if forum is None:
        return None
    return {
        "source": forum.source,
        "targetDate": forum.target_date.isoformat(),
        "threadUrl": forum.thread_url,
        "postCount": forum.post_count,
        "sameDayPostCount": forum.same_day_post_count,
        "previousDayTomorrowPostCount": forum.previous_day_tomorrow_post_count,
        "latestPostAt": _iso(forum.latest_post_at),
        "locations": forum.locations,
        "signals": forum.signals,
        "summary": forum.summary,
        "unavailableReason": forum.unavailable_reason,
        "examples": [
            {"author": post.author, "publishedAt": _iso(post.published_at), "url": post.url, "text": post.text[:220]}
            for post in forum.posts[:3]
        ],
    }


def _recent_observations_payload(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows[-12:]:
        result.append(
            {
                "time": row.get("valid"),
                "temperatureC": _safe_float(row.get("tmpc")),
                "metar": row.get("metar"),
            }
        )
    return result


def _source_health_payload(item: SourceHealth) -> dict[str, Any]:
    return {
        "source": item.source,
        "state": item.state.value,
        "checkedAt": _iso(item.checked_at),
        "latencyMs": round(item.latency_ms) if item.latency_ms is not None else None,
        "message": item.message,
    }


def _method_cards() -> list[dict[str, str]]:
    return [
        {
            "title": "Airport-first",
            "body": "Ankara şehir merkezi değil, LTAC/Esenboğa koordinatı ve METAR anchor kullanılır.",
        },
        {
            "title": "Dynamic blend",
            "body": "ICON-EU, ECMWF, ICON-Global, GFS ve keyed fallback modeller ağırlık/bias katmanına girer.",
        },
        {
            "title": "Gaussian buckets",
            "body": "Polymarket bracket olasılığı final tmax ve sigma üzerinden hesaplanır; edge market fiyatıyla kıyaslanır.",
        },
        {
            "title": "Manual trading safe",
            "body": "Trading env hazır ama varsayılan kapalı; dashboard karar destek ekranı olarak çalışır.",
        },
    ]


def _env_configured(settings: Settings, env_name: str | None) -> bool:
    if env_name is None:
        return True
    attr = env_name.lower()
    if env_name == "XWEATHER_CLIENT_ID":
        return bool(settings.xweather_client_id and settings.xweather_client_secret)
    value = getattr(settings, attr, None)
    return bool(value)


def _peak_point(points: list[ModelHourlyPoint]) -> ModelHourlyPoint | None:
    values = [point for point in points if point.temperature_2m_c is not None]
    if not values:
        return None
    return max(values, key=lambda point: point.temperature_2m_c or -999)


def _boundary_risk(final_tmax_c: float | None) -> str:
    if final_tmax_c is None:
        return "veri yok"
    distance = abs(final_tmax_c - (int(final_tmax_c) + 0.5))
    if distance < 0.18:
        return "yüksek"
    if distance < 0.35:
        return "orta"
    return "düşük"


def _confidence_label(score: int) -> str:
    if score >= 75:
        return "yüksek"
    if score >= 55:
        return "orta"
    if score >= 40:
        return "düşük"
    return "kritik"


def _display_model_name(model: str) -> str:
    lowered = model.lower()
    if "icon_eu" in lowered:
        return "ICON-EU"
    if "icon_global" in lowered:
        return "ICON-Global"
    if "ecmwf" in lowered:
        return "ECMWF"
    if "gfs" in lowered:
        return "GFS"
    if "visual_crossing" in lowered:
        return "Visual Crossing"
    if "tomorrow" in lowered:
        return "Tomorrow.io"
    if "openweather" in lowered:
        return "OpenWeather"
    if "weatherbit" in lowered:
        return "Weatherbit"
    return model


def _adjustment_label(name: str) -> str:
    return {
        "live_observation": "Canlı sapma",
        "advection": "Rüzgâr/adveksiyon",
        "synoptic_pressure": "Basınç/üst seviye",
        "cloud_radiation": "Bulut/radyasyon",
        "rain_soil": "Yağış/zemin",
        "ai_effect_analysis": "AI etki",
        "ltac_microclimate": "LTAC mikroklima",
        "uncertainty": "Belirsizlik",
    }.get(name, name.replace("_", " ").title())


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
