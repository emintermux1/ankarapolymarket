from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.aviationweather import AviationWeatherAdapter
from src.data_sources.checkwx import CheckWXAdapter
from src.data_sources.herbie_optional import unavailable_health as herbie_unavailable_health
from src.data_sources.iem_asos import IEMASOSAdapter
from src.data_sources.mgm_optional import unavailable_health as mgm_unavailable_health
from src.data_sources.openmeteo import OpenMeteoAdapter
from src.data_sources.polymarket import PolymarketAviationReader
from src.data_sources.schemas import (
    ActualResult,
    ForecastAnalysis,
    MarketSnapshot,
    METARNormalized,
    ModelBundle,
    ModelForecast,
    SourceHealth,
    TAFNormalized,
)
from src.data_sources.tomorrow import TomorrowIOAdapter
from src.data_sources.visualcrossing import VisualCrossingAdapter
from src.data_sources.wunderground import WundergroundScraper
from src.db.repository import Repository, manual_actual_result
from src.forecast.engine import LTACForecastEngine
from src.reports.charts import ChartRenderer
from src.reports.telegram_renderer import TelegramReportRenderer


@dataclass
class ForecastContext:
    analysis: ForecastAnalysis
    metar: METARNormalized | None
    taf: TAFNormalized | None
    model_bundle: ModelBundle | None
    market: MarketSnapshot | None


class ForecastService:
    def __init__(self, settings: Settings, repository: Repository) -> None:
        self.settings = settings
        self.repository = repository
        self.aviation = AviationWeatherAdapter(settings)
        self.checkwx = CheckWXAdapter(settings)
        self.openmeteo = OpenMeteoAdapter(settings)
        self.visualcrossing = VisualCrossingAdapter(settings)
        self.tomorrow = TomorrowIOAdapter(settings)
        self.polymarket = PolymarketAviationReader(settings)
        self.iem = IEMASOSAdapter(settings)
        self.wunderground = WundergroundScraper(settings)
        self.engine = LTACForecastEngine(settings)
        self.renderer = TelegramReportRenderer(settings)
        self.charts = ChartRenderer(settings)

    def default_target_date(self) -> date:
        return datetime.now(ZoneInfo(self.settings.report_timezone)).date()

    async def build_forecast_context(self, target_date: date | None = None, report_label: str = "manual") -> ForecastContext:
        target = target_date or self.default_target_date()
        metar, taf, bundle, market = await asyncio.gather(
            self._safe_metar(),
            self._safe_taf(),
            self._safe_models(target),
            self._safe_market(target),
        )
        if metar:
            self.repository.save_observation(metar)
        if taf:
            self.repository.save_taf(taf)
        if bundle:
            self.repository.save_model_bundle(bundle)
        self.repository.save_market_snapshot(market)
        historical_weights = self.repository.latest_model_weights(self.settings.openmeteo_models)
        analysis = self.engine.run(
            target_date=target,
            metar=metar,
            taf=taf,
            model_bundle=bundle or ModelBundle(fetch_timestamp=datetime.now(timezone.utc), target_date=target),
            market=market,
            historical_weights=historical_weights,
        )
        self.repository.save_forecast_analysis(analysis, report_label=report_label)
        return ForecastContext(analysis=analysis, metar=metar, taf=taf, model_bundle=bundle, market=market)

    async def render_daily_report(self, target_date: date | None = None, report_label: str = "09:00") -> str:
        ctx = await self.build_forecast_context(target_date=target_date, report_label=report_label)
        report = self.renderer.daily_report(
            analysis=ctx.analysis,
            metar=ctx.metar,
            taf=ctx.taf,
            model_bundle=ctx.model_bundle,
            market=ctx.market,
            report_label=report_label,
        )
        return report

    async def render_now(self) -> str:
        metar = await self._safe_metar()
        if metar:
            self.repository.save_observation(metar)
        return self.renderer.now_report(metar)

    async def render_taf(self) -> str:
        taf = await self._safe_taf()
        if taf:
            self.repository.save_taf(taf)
        return self.renderer.taf_report(taf)

    async def render_models(self, target_date: date | None = None) -> str:
        target = target_date or self.default_target_date()
        bundle = await self._safe_models(target)
        if bundle:
            self.repository.save_model_bundle(bundle)
        return self.renderer.models_report(bundle)

    async def render_advanced_signals(self, target_date: date | None = None) -> str:
        target = target_date or self.default_target_date()
        bundle = await self._safe_models(target)
        if bundle:
            self.repository.save_model_bundle(bundle)
        return self.renderer.advanced_signals_report(bundle)

    async def render_market(self, target_date: date | None = None) -> str:
        target = target_date or self.default_target_date()
        market = await self._safe_market(target)
        self.repository.save_market_snapshot(market)
        prediction = self.repository.latest_prediction(target)
        analysis = ForecastAnalysis.model_validate(prediction) if prediction else None
        return self.renderer.market_report(analysis, market)

    async def render_edge(self, target_date: date | None = None) -> str:
        ctx = await self.build_forecast_context(target_date=target_date, report_label="edge")
        return self.renderer.market_report(ctx.analysis, ctx.market)

    def render_backtest(self) -> str:
        return self.renderer.backtest_report(self.repository.latest_backtest_summary())

    async def render_sources(self) -> str:
        health = await self.check_sources()
        return self.renderer.sources_report(health)

    async def render_result(self, target_date: date | None = None) -> str:
        target = target_date or self.default_target_date()
        if self.settings.visualcrossing_api_key:
            result = await self.visualcrossing.get_daily_result(target)
            if result.tmax_c is None:
                result = await self.wunderground.get_daily_result(target)
        else:
            result = await self.wunderground.get_daily_result(target)
        self.repository.save_actual_result(result)
        return self.renderer.result_report(result)

    def save_manual_result(self, target_date: date, tmax_c: float) -> str:
        result = manual_actual_result(target_date, tmax_c)
        self.repository.save_actual_result(result)
        return self.renderer.result_report(result)

    async def model_chart(self, target_date: date | None = None) -> tuple[str, str]:
        target = target_date or self.default_target_date()
        metar, bundle = await asyncio.gather(self._safe_metar(), self._safe_models(target))
        if bundle is None:
            raise RuntimeError("model data unavailable")
        path = self.charts.model_comparison(bundle)
        return str(path), f"LTAC model comparison chart - {target.isoformat()}"

    async def observed_chart(self, target_date: date | None = None) -> tuple[str, str]:
        target = target_date or self.default_target_date()
        metar, bundle = await asyncio.gather(self._safe_metar(), self._safe_models(target))
        if bundle is None:
            raise RuntimeError("model data unavailable")
        path = self.charts.observed_vs_forecast(bundle, metar)
        return str(path), f"LTAC observed vs forecast chart - {target.isoformat()}"

    async def check_sources(self) -> list[SourceHealth]:
        health = await asyncio.gather(
            self.aviation.health(),
            self.checkwx.health(),
            self.openmeteo.health(),
            self.visualcrossing.health(),
            self.tomorrow.health(),
            self.polymarket.health(),
            self.iem.health(),
            self.wunderground.health(),
        )
        optional_health = [mgm_unavailable_health(), herbie_unavailable_health()]
        for item in [*health, *optional_health]:
            self.repository.save_source_health(item)
        return [*health, *optional_health]

    async def _safe_metar(self) -> METARNormalized | None:
        try:
            return await self.aviation.get_metar()
        except Exception:
            try:
                return await self.checkwx.get_metar()
            except Exception:
                return None

    async def _safe_taf(self) -> TAFNormalized | None:
        try:
            return await self.aviation.get_taf()
        except Exception:
            try:
                return await self.checkwx.get_taf()
            except Exception:
                return None

    async def _safe_models(self, target_date: date) -> ModelBundle | None:
        bundle, visual, tomorrow = await asyncio.gather(
            self._safe_openmeteo_models(target_date),
            self._safe_visualcrossing_model(target_date),
            self._safe_tomorrow_model(target_date),
        )
        extras = [forecast for forecast in (visual, tomorrow) if forecast is not None]
        if bundle is None and extras:
            bundle = ModelBundle(fetch_timestamp=datetime.now(timezone.utc), target_date=target_date, source="Mixed")
        if bundle is not None:
            bundle.forecasts.extend(extras)
        return bundle

    async def _safe_openmeteo_models(self, target_date: date) -> ModelBundle | None:
        try:
            return await self.openmeteo.get_bundle_with_ensemble(target_date)
        except Exception:
            return None

    async def _safe_visualcrossing_model(self, target_date: date) -> ModelForecast | None:
        if not self.settings.visualcrossing_api_key:
            return None
        try:
            return await self.visualcrossing.get_model_forecast(target_date)
        except Exception:
            return None

    async def _safe_tomorrow_model(self, target_date: date) -> ModelForecast | None:
        if not self.settings.tomorrow_api_key:
            return None
        try:
            return await self.tomorrow.get_model_forecast(target_date)
        except Exception:
            return None

    async def _safe_market(self, target_date: date) -> MarketSnapshot | None:
        try:
            return await self.polymarket.get_market(target_date)
        except Exception:
            return None
