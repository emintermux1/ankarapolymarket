from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.schemas import (
    ActualResult,
    ForecastAnalysis,
    MarketSnapshot,
    METARNormalized,
    ModelBundle,
    SourceHealth,
    TAFNormalized,
)


class TelegramReportRenderer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tz = ZoneInfo(settings.report_timezone)

    def daily_report(
        self,
        *,
        analysis: ForecastAnalysis,
        metar: METARNormalized | None,
        taf: TAFNormalized | None,
        model_bundle: ModelBundle | None,
        market: MarketSnapshot | None,
    ) -> str:
        report_time = analysis.generated_at.astimezone(self.tz)
        market_lines = self._market_lines(analysis, market)
        model_lines = self._model_lines(model_bundle, analysis)
        dynamic_lines = self._dynamic_lines(analysis)
        bullets = "\n".join(f"* {bullet}" for bullet in analysis.rationale_bullets) or "Veri eksik; gerekçe üretilemedi."
        return "\n".join(
            [
                "ANKARA ESENBOĞA GÜNLÜK MAKSİMUM SICAKLIK TAHMİNİ",
                "",
                f"Tarih: {analysis.target_date.isoformat()}",
                f"Rapor saati: {report_time:%H:%M} {self.settings.report_timezone}",
                "Lokasyon: Ankara Esenboğa / LTAC",
                "",
                "Final tahmin:",
                f"Beklenen maksimum sıcaklık: {_fmt_c(analysis.final_tmax_c)}",
                f"Ana aralık: {_fmt_c(analysis.main_range_low_c)} - {_fmt_c(analysis.main_range_high_c)}",
                f"Güven skoru: {analysis.confidence_score}/100",
                f"Boundary risk: {_boundary_risk(analysis)}",
                f"Kısa karar: {analysis.verdict}",
                "",
                "Canlı gözlem:",
                *self._metar_lines(metar),
                "",
                "Model tahminleri:",
                *model_lines,
                "",
                "Hava dinamiği:",
                *dynamic_lines,
                "",
                "Neden bu tahmin?",
                bullets,
                "",
                "Market fiyatlaması:",
                *market_lines,
                "",
                "MANUEL BET ÖZETİ ($100 sabit)",
                *self._manual_bet_lines(analysis, market),
                "",
                "Riskler:",
                f"Yukarı risk: {analysis.risks.get('upward', 'unavailable')}",
                f"Aşağı risk: {analysis.risks.get('downward', 'unavailable')}",
                f"En kritik belirsizlik: {analysis.risks.get('critical', 'unavailable')}",
                "",
                "Kaynak durumu:",
                f"AviationWeather: {'ok' if metar else 'unavailable'}",
                f"Open-Meteo: {'ok' if model_bundle and model_bundle.available_forecasts else 'unavailable'}",
                f"Polymarket: {'ok' if market and market.valid_for_target else 'ilgili market bulunamadı'}",
                f"TAF: {'ok' if taf else 'unavailable'}",
            ]
        )

    def now_report(self, metar: METARNormalized | None) -> str:
        if metar is None:
            return "LTAC METAR unavailable."
        return "\n".join(["LTAC SON GÖZLEM", *self._metar_lines(metar)])

    def taf_report(self, taf: TAFNormalized | None) -> str:
        if taf is None:
            return "LTAC TAF unavailable."
        periods = []
        for period in taf.periods[:5]:
            periods.append(
                f"* {period.time_from.astimezone(self.tz):%d %H:%M}-{period.time_to.astimezone(self.tz):%d %H:%M}: "
                f"{period.change or 'BASE'} {period.weather or ''} rüzgâr {period.wind_direction_deg or 'VRB'}°/{period.wind_speed_kt or 0:.0f} kt"
            )
        return "\n".join(
            [
                "LTAC TAF",
                f"Yayın: {taf.issue_time.astimezone(self.tz):%Y-%m-%d %H:%M}",
                f"Raw: {taf.raw_text}",
                "",
                *periods,
            ]
        )

    def models_report(self, bundle: ModelBundle | None) -> str:
        if bundle is None:
            return "Model verisi unavailable."
        return "\n".join(["MODEL KARŞILAŞTIRMA", *self._model_lines(bundle)])

    def market_report(self, analysis: ForecastAnalysis | None, market: MarketSnapshot | None) -> str:
        if market is None:
            return "ilgili market bulunamadı"
        lines = [
            "POLYMARKET FİYATLAMA",
            f"Başlık: {market.title}",
            f"Link: {market.link}",
            f"Hacim: ${_fmt_num(market.volume)}",
            f"Likidite: ${_fmt_num(market.liquidity)}",
            f"Geçerlilik: {'ok' if market.valid_for_target else market.validation_message}",
        ]
        if analysis:
            lines.append(f"Edge: {analysis.edge_summary}")
        for outcome in market.outcomes:
            lines.append(f"* {outcome.bracket}: {_fmt_pct(outcome.implied_probability)} spread {_fmt_num(outcome.spread)}")
        lines.append("Not: Yatırım tavsiyesi değildir.")
        return "\n".join(lines)

    def sources_report(self, health: list[SourceHealth]) -> str:
        if not health:
            return "Kaynak durumu henüz kaydedilmedi."
        lines = ["KAYNAK DURUMU"]
        for item in health:
            suffix = f" ({item.message})" if item.message else ""
            latency = f", {item.latency_ms:.0f} ms" if item.latency_ms is not None else ""
            lines.append(f"* {item.source}: {item.state.value}{latency}{suffix}")
        return "\n".join(lines)

    def backtest_report(self, rows: list[dict]) -> str:
        if not rows:
            return "Backtest için henüz yeterli geçmiş yok."
        lines = ["BACKTEST ÖZETİ"]
        for row in rows[:10]:
            lines.append(
                f"* {row['model']} {row['window_days']}g: MAE {_fmt_num(row['mae'])}, bias {_fmt_num(row['bias'])}, kalibrasyon {_fmt_num(row['calibration_score'])}"
            )
        return "\n".join(lines)

    def result_report(self, result: ActualResult) -> str:
        if result.tmax_c is None:
            return f"Final sonuç unavailable: {result.unavailable_reason or 'bilinmiyor'}"
        return "\n".join(
            [
                "GÜN SONU SONUÇ",
                f"Tarih: {result.target_date.isoformat()}",
                f"Kaynak: {result.source}",
                f"Final Tmax: {result.tmax_c:.1f}°C",
                f"Market rounding: {result.rounded_tmax_c}°C",
            ]
        )

    def _metar_lines(self, metar: METARNormalized | None) -> list[str]:
        if metar is None:
            return [
                "* Son METAR: unavailable",
                "* Gözlem zamanı: unavailable",
                "* Sıcaklık: unavailable",
                "* Çiğ noktası: unavailable",
                "* Nem: unavailable",
                "* Rüzgâr: unavailable",
                "* Basınç: unavailable",
                "* Bulut: unavailable",
                "* Görüş: unavailable",
            ]
        return [
            f"* Son METAR: {metar.raw_text}",
            f"* Gözlem zamanı: {metar.observation_time:%Y-%m-%d %H:%M UTC}",
            f"* Sıcaklık: {metar.temperature_c:.1f}°C",
            f"* Çiğ noktası: {metar.dew_point_c:.1f}°C",
            f"* Nem: %{metar.relative_humidity if metar.relative_humidity is not None else 'unavailable'}",
            f"* Rüzgâr: {metar.wind_direction_deg if metar.wind_direction_deg is not None else 'VRB'}° / {metar.wind_speed_kt:.0f} KT",
            f"* Basınç: {_fmt_num(metar.pressure_hpa)} hPa",
            f"* Bulut: {_format_clouds(metar.cloud_layers)}",
            f"* Görüş: {metar.visibility_m if metar.visibility_m is not None else 'unavailable'}m",
        ]

    def _model_lines(self, bundle: ModelBundle | None, analysis: ForecastAnalysis | None = None) -> list[str]:
        if bundle is None:
            return ["* ECMWF: unavailable", "* GFS: unavailable", "* ICON: unavailable", "* Model spread: unavailable"]
        lines = []
        name_map = {
            "icon_eu": "ICON-EU",
            "icon_global": "ICON-Global",
            "ecmwf": "ECMWF",
            "gfs": "GFS",
            "icon": "ICON",
        }
        for forecast in bundle.forecasts:
            label = next((display for key, display in name_map.items() if key in forecast.model.lower()), forecast.model)
            weight = ""
            if analysis and forecast.model in analysis.model_weights:
                weight = f" / ağırlık %{analysis.model_weights[forecast.model] * 100:.0f}"
            lines.append(f"* {label}: {_fmt_c(forecast.tmax_c) if forecast.available else 'unavailable'}{weight}")
        values = [forecast.tmax_c for forecast in bundle.available_forecasts if forecast.tmax_c is not None]
        if values:
            lines.append(f"* Model aralığı: {min(values):.1f}°C - {max(values):.1f}°C")
        else:
            lines.append("* Model aralığı: unavailable")
        if analysis and analysis.ensemble_sigma_c is not None:
            lines.append(f"* Ensemble sigma: {analysis.ensemble_sigma_c:.1f}°C")
        if analysis and analysis.probability_sigma_c is not None:
            lines.append(f"* Probability sigma: {analysis.probability_sigma_c:.1f}°C")
        return lines

    def _dynamic_lines(self, analysis: ForecastAnalysis) -> list[str]:
        lookup = {item.name: item for item in analysis.adjustments}
        return [
            f"* Rüzgâr/adveksiyon: {_adj(lookup.get('advection'))}",
            f"* Bulut/radyasyon: {_adj(lookup.get('cloud_radiation'))}",
            f"* Yağış riski: {_adj(lookup.get('rain_soil'))}",
            f"* LTAC mikroklima: {_adj(lookup.get('ltac_microclimate'))}",
        ]

    def _market_lines(self, analysis: ForecastAnalysis, market: MarketSnapshot | None) -> list[str]:
        if market is None or not market.valid_for_target:
            return [
                "* Link: ilgili market bulunamadı",
                "* Outcome fiyatları: unavailable",
                "* Hacim: unavailable",
                "* Likidite: unavailable",
                "* Edge: Edge yok",
                "* Not: Yatırım tavsiyesi değildir.",
            ]
        lines = [
            f"* Link: {market.link}",
            f"* Hacim: ${_fmt_num(market.volume)}",
            f"* Likidite: ${_fmt_num(market.liquidity)}",
        ]
        for outcome in market.outcomes:
            implied = outcome.implied_probability
            fair = analysis.fair_probabilities.get(outcome.bracket)
            edge = fair - implied if fair is not None and implied is not None else None
            details = [_fmt_cents(implied)]
            if fair is not None:
                details.append(f"fair {_fmt_pct(fair)}")
            if edge is not None:
                details.append(f"edge {_fmt_pp(edge)}")
            lines.append(f"* {outcome.bracket}: {', '.join(details)}")
        lines.append(f"* {analysis.edge_summary}")
        lines.append("* Not: Yatırım tavsiyesi değildir.")
        return lines

    def _manual_bet_lines(self, analysis: ForecastAnalysis, market: MarketSnapshot | None) -> list[str]:
        candidate = _best_market_candidate(analysis, market)
        boundary = _boundary_risk(analysis)
        if candidate is None:
            return [
                "* Önerilen bracket: BET YOK",
                "* Karar: SKIP",
                "* Sebep: geçerli fiyat/fair probability ile pozitif edge bulunamadı.",
                "* Not: Yatırım tavsiyesi değildir; manuel karar sende.",
            ]

        edge, bracket, fair, implied = candidate
        reasons = []
        if edge < 0.05:
            reasons.append("edge eşiği altında")
        if boundary == "HIGH":
            reasons.append("boundary HIGH")
        if implied <= 0.0 or implied >= 1.0:
            reasons.append("piyasa fiyatı geçersiz")
        should_bet = not reasons

        lines = [
            f"* Önerilen bracket: {bracket if should_bet else 'BET YOK'}",
            f"* En iyi aday{'' if should_bet else ' (işlem yok)'}: {bracket}",
            f"* Market fiyat: {_fmt_cents(implied)}",
            f"* Bot fair prob: {_fmt_pct(fair)}",
            f"* Edge: {_fmt_pp(edge)}",
            f"* Boundary risk: {boundary}",
        ]
        if should_bet:
            lines.append(f"* Beklenen EV: ${_fmt_num(_expected_profit_usd(100.0, fair, implied))}")
            lines.append("* Karar: MANUEL ONAY GEREKİR")
        else:
            lines.append("* Beklenen EV: gösterilmiyor (SKIP)")
            lines.append(f"* Karar: SKIP ({'; '.join(reasons)})")
        lines.append("* Not: Yatırım tavsiyesi değildir; manuel karar sende.")
        return lines


def _fmt_c(value: float | None) -> str:
    return f"{value:.1f}°C" if value is not None else "unavailable"


def _fmt_num(value: float | int | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _fmt_pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "unavailable"


def _fmt_cents(value: float | None) -> str:
    return f"{_fmt_num(value * 100)}¢" if value is not None else "unavailable"


def _fmt_pp(value: float | None) -> str:
    return f"{value * 100:+.1f} pp" if value is not None else "unavailable"


def _best_market_candidate(analysis: ForecastAnalysis, market: MarketSnapshot | None) -> tuple[float, str, float, float] | None:
    if market is None or not market.valid_for_target:
        return None
    best: tuple[float, str, float, float] | None = None
    for outcome in market.outcomes:
        implied = outcome.implied_probability
        fair = analysis.fair_probabilities.get(outcome.bracket)
        if fair is None or implied is None:
            continue
        edge = fair - implied
        if best is None or edge > best[0]:
            best = (edge, outcome.bracket, fair, implied)
    return best


def _boundary_risk(analysis: ForecastAnalysis) -> str:
    if analysis.final_tmax_c is None:
        return "unavailable"
    nearest_half_degree_distance = round(abs((analysis.final_tmax_c - 0.5) - round(analysis.final_tmax_c - 0.5)), 3)
    sigma = analysis.probability_sigma_c or 0.0
    if nearest_half_degree_distance <= 0.3 or sigma >= 1.4:
        return "HIGH"
    if nearest_half_degree_distance <= 0.45 or sigma >= 0.9:
        return "MEDIUM"
    return "LOW"


def _expected_profit_usd(stake_usd: float, fair_probability: float, yes_price: float) -> float | None:
    if yes_price <= 0.0 or yes_price >= 1.0:
        return None
    shares = stake_usd / yes_price
    return fair_probability * shares - stake_usd


def _format_clouds(clouds: list[dict]) -> str:
    if not clouds:
        return "unavailable"
    return ", ".join(f"{cloud.get('cover', '?')}{cloud.get('base', '')}" for cloud in clouds)


def _adj(adjustment: object | None) -> str:
    if adjustment is None:
        return "unavailable"
    return f"{adjustment.summary} ({adjustment.value_c:+.1f}°C)"
