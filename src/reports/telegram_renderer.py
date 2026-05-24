from __future__ import annotations

from datetime import date
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
        report_label: str | None = None,
    ) -> str:
        report_time = analysis.generated_at.astimezone(self.tz)
        return "\n".join(
            [
                _report_title(report_label),
                "",
                f"Tarih: {analysis.target_date.isoformat()}",
                f"Rapor saati: {report_time:%H:%M} ({self.settings.report_timezone})",
                "Lokasyon: Ankara Esenboğa / LTAC",
                "",
                "Özet:",
                _bullet(f"Beklenen maksimum: {_fmt_c(analysis.final_tmax_c)}"),
                _bullet(f"Ana aralık: {_fmt_range(analysis.main_range_low_c, analysis.main_range_high_c)}"),
                _bullet(f"Güven: {analysis.confidence_score}/100 ({_confidence_label(analysis.confidence_score)})"),
                _bullet(f"Sınır riski: {_boundary_risk(analysis)}"),
                _bullet(f"Karar: {analysis.verdict}"),
                "",
                "Veri kontrolü:",
                *self._data_quality_lines(analysis.target_date, metar, taf, model_bundle, market),
                "",
                "Canlı gözlem:",
                *self._metar_lines(metar, include_raw=False),
                "",
                "Model tahminleri:",
                *self._model_lines(model_bundle, analysis),
                "",
                "Hava dinamiği:",
                *self._dynamic_lines(analysis),
                "",
                "AI Etki Analizi:",
                *self._ai_effect_lines(analysis),
                "",
                "Neden bu tahmin?",
                *self._rationale_lines(analysis),
                "",
                "Market fiyatlaması:",
                *self._market_lines(analysis, market),
                "",
                "MANUEL BET ÖZETİ ($100 sabit)",
                *self._manual_bet_lines(analysis, market),
                "",
                "Riskler:",
                f"Yukarı risk: {analysis.risks.get('upward', 'veri yok')}",
                f"Aşağı risk: {analysis.risks.get('downward', 'veri yok')}",
                f"En kritik belirsizlik: {analysis.risks.get('critical', 'veri yok')}",
            ]
        )

    def now_report(self, metar: METARNormalized | None) -> str:
        if metar is None:
            return "LTAC METAR verisi yok."
        return "\n".join(["LTAC SON GÖZLEM", *self._metar_lines(metar)])

    def taf_report(self, taf: TAFNormalized | None) -> str:
        if taf is None:
            return "LTAC TAF verisi yok."
        periods = []
        for period in taf.periods[:5]:
            periods.append(
                _bullet(
                    f"{period.time_from.astimezone(self.tz):%d %H:%M}-{period.time_to.astimezone(self.tz):%d %H:%M}: "
                    f"{period.change or 'BASE'} {period.weather or ''} rüzgâr {period.wind_direction_deg or 'VRB'}°/{period.wind_speed_kt or 0:.0f} kt"
                )
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
            return "Model verisi yok."
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
            f"Geçerlilik: {'uygun' if market.valid_for_target else market.validation_message}",
        ]
        if analysis:
            lines.append(f"Edge: {analysis.edge_summary}")
        for outcome in market.outcomes:
            lines.append(_bullet(f"{outcome.bracket}: {_fmt_pct(outcome.implied_probability)} makas {_fmt_num(outcome.spread)}"))
        lines.append("Not: Yatırım tavsiyesi değildir.")
        return "\n".join(lines)

    def sources_report(self, health: list[SourceHealth]) -> str:
        if not health:
            return "Kaynak durumu henüz kaydedilmedi."
        lines = ["KAYNAK DURUMU"]
        for item in health:
            suffix = f" ({item.message})" if item.message else ""
            latency = f", {item.latency_ms:.0f} ms" if item.latency_ms is not None else ""
            lines.append(_bullet(f"{item.source}: {_source_state_label(item.state.value)}{latency}{suffix}"))
        return "\n".join(lines)

    def backtest_report(self, rows: list[dict]) -> str:
        if not rows:
            return "Backtest için henüz yeterli geçmiş yok."
        lines = ["BACKTEST ÖZETİ"]
        for row in rows[:10]:
            lines.append(
                _bullet(f"{row['model']} {row['window_days']}g: MAE {_fmt_num(row['mae'])}, bias {_fmt_num(row['bias'])}, kalibrasyon {_fmt_num(row['calibration_score'])}")
            )
        return "\n".join(lines)

    def result_report(self, result: ActualResult) -> str:
        if result.tmax_c is None:
            return f"Final sonuç verisi yok: {result.unavailable_reason or 'bilinmiyor'}"
        return "\n".join(
            [
                "GÜN SONU SONUÇ",
                f"Tarih: {result.target_date.isoformat()}",
                f"Kaynak: {result.source}",
                f"Final Tmax: {result.tmax_c:.1f}°C",
                f"Market rounding: {result.rounded_tmax_c}°C",
            ]
        )

    def _metar_lines(self, metar: METARNormalized | None, *, include_raw: bool = True) -> list[str]:
        if metar is None:
            return [
                _bullet("Son METAR: veri yok"),
                _bullet("Gözlem zamanı: veri yok"),
                _bullet("Sıcaklık: veri yok"),
                _bullet("Çiğ noktası: veri yok"),
                _bullet("Nem: veri yok"),
                _bullet("Rüzgâr: veri yok"),
                _bullet("Basınç: veri yok"),
                _bullet("Bulut: veri yok"),
                _bullet("Görüş: veri yok"),
            ]
        lines = [
            _bullet(f"Gözlem zamanı: {metar.observation_time:%Y-%m-%d %H:%M UTC}"),
            _bullet(f"Sıcaklık: {metar.temperature_c:.1f}°C"),
            _bullet(f"Çiğ noktası: {metar.dew_point_c:.1f}°C"),
            _bullet(f"Nem: %{metar.relative_humidity if metar.relative_humidity is not None else 'veri yok'}"),
            _bullet(f"Rüzgâr: {metar.wind_direction_deg if metar.wind_direction_deg is not None else 'VRB'}° / {metar.wind_speed_kt:.0f} KT"),
            _bullet(f"Basınç: {_fmt_num(metar.pressure_hpa)} hPa"),
            _bullet(f"Bulut: {_format_clouds(metar.cloud_layers)}"),
            _bullet(f"Görüş: {metar.visibility_m if metar.visibility_m is not None else 'veri yok'}m"),
        ]
        if include_raw:
            return [_bullet(f"Son METAR: {metar.raw_text}"), *lines]
        return lines

    def _model_lines(self, bundle: ModelBundle | None, analysis: ForecastAnalysis | None = None) -> list[str]:
        if bundle is None:
            return [_bullet("ECMWF: veri yok"), _bullet("GFS: veri yok"), _bullet("ICON: veri yok"), _bullet("Model aralığı: veri yok")]
        lines = []
        for forecast in bundle.forecasts:
            label = _display_model_name(forecast.model)
            weight = ""
            if analysis and forecast.model in analysis.model_weights:
                weight = f" (ağırlık %{analysis.model_weights[forecast.model] * 100:.0f})"
            value = _fmt_c(forecast.tmax_c) if forecast.available else "veri yok"
            reason = f" - {forecast.unavailable_reason}" if not forecast.available and forecast.unavailable_reason else ""
            lines.append(_bullet(f"{label}: {value}{weight}{reason}"))
        values = [forecast.tmax_c for forecast in bundle.available_forecasts if forecast.tmax_c is not None]
        if values:
            lines.append(_bullet(f"Model aralığı: {min(values):.1f}°C - {max(values):.1f}°C"))
        else:
            lines.append(_bullet("Model aralığı: veri yok"))
        if analysis and analysis.ensemble_sigma_c is not None:
            lines.append(_bullet(f"Ensemble belirsizliği: ±{analysis.ensemble_sigma_c:.1f}°C"))
        if analysis and analysis.probability_sigma_c is not None:
            lines.append(_bullet(f"Olasılık hesabı belirsizliği: ±{analysis.probability_sigma_c:.1f}°C"))
        return lines

    def _dynamic_lines(self, analysis: ForecastAnalysis) -> list[str]:
        lookup = {item.name: item for item in analysis.adjustments}
        return [
            _bullet(f"Canlı sapma: {_adj(lookup.get('live_observation'))}"),
            _bullet(f"Rüzgâr/adveksiyon: {_adj(lookup.get('advection'))}"),
            _bullet(f"Bulut/radyasyon: {_adj(lookup.get('cloud_radiation'))}"),
            _bullet(f"Yağış/zemin: {_adj(lookup.get('rain_soil'))}"),
        ]

    def _ai_effect_lines(self, analysis: ForecastAnalysis) -> list[str]:
        adjustment = next((item for item in analysis.adjustments if item.name == "ai_effect_analysis"), None)
        bullets = adjustment.inputs.get("bullets") if adjustment else None
        if not isinstance(bullets, list) or not bullets:
            return [_bullet("CAPE/CIN/rüzgâr etki analizi: veri yok")]
        return [_bullet(str(item)) for item in bullets]

    def _rationale_lines(self, analysis: ForecastAnalysis) -> list[str]:
        if not analysis.rationale_bullets:
            return [_bullet("Veri eksik; gerekçe üretilemedi.")]
        return [_bullet(bullet) for bullet in analysis.rationale_bullets]

    def _market_lines(self, analysis: ForecastAnalysis, market: MarketSnapshot | None) -> list[str]:
        if market is None:
            return [
                _bullet("Polymarket link: ilgili market bulunamadı"),
                _bullet("Piyasa fiyatları: veri yok"),
                _bullet("Hacim: veri yok"),
                _bullet("Spread: veri yok"),
                _bullet("Likidite: veri yok"),
                _bullet("Edge: Edge yok"),
                _bullet("Not: Yatırım tavsiyesi değildir."),
            ]
        if not market.valid_for_target:
            return [
                _bullet(f"Polymarket link: {market.link}"),
                _bullet(f"Durum: hedefle uyumsuz ({market.validation_message or 'neden yok'})"),
                _bullet("Piyasa fiyatları: veri yok"),
                _bullet("Hacim: veri yok"),
                _bullet("Spread: veri yok"),
                _bullet("Likidite: veri yok"),
                _bullet("Edge: Edge yok"),
                _bullet("Not: Yatırım tavsiyesi değildir."),
            ]
        ranked_outcomes = sorted(
            market.outcomes,
            key=lambda outcome: outcome.implied_probability or -1,
            reverse=True,
        )
        spreads = [outcome.spread for outcome in market.outcomes if outcome.spread is not None]
        lines = [
            _bullet(f"Polymarket link: {market.link}"),
            _bullet(f"Hacim: ${_fmt_num(market.volume)}"),
            _bullet(f"Likidite: ${_fmt_num(market.liquidity)}"),
            _bullet(f"En geniş spread: {_fmt_num(max(spreads) if spreads else None)}"),
            _bullet("En güçlü piyasa fiyatları:"),
        ]
        for outcome in ranked_outcomes[:3]:
            implied = outcome.implied_probability
            fair = analysis.fair_probabilities.get(outcome.bracket)
            edge = fair - implied if fair is not None and implied is not None else None
            details = [_fmt_cents(implied)]
            if fair is not None:
                details.append(f"fair {_fmt_pct(fair)}")
            if edge is not None:
                details.append(f"edge {_fmt_pp(edge)}")
            lines.append(_bullet(f"{outcome.bracket}: {', '.join(details)}"))
        if len(ranked_outcomes) > 3:
            lines.append(_bullet(f"Diğer outcome sayısı: {len(ranked_outcomes) - 3}"))
        lines.append(_bullet(f"Edge: {analysis.edge_summary}"))
        lines.append(_bullet("Not: Yatırım tavsiyesi değildir."))
        return lines

    def _manual_bet_lines(self, analysis: ForecastAnalysis, market: MarketSnapshot | None) -> list[str]:
        candidate = _best_market_candidate(analysis, market)
        boundary = _boundary_risk(analysis)
        if candidate is None:
            return [
                _bullet("Önerilen bracket: BET YOK"),
                _bullet("Karar: SKIP"),
                _bullet("Sebep: geçerli fiyat/fair probability ile pozitif edge bulunamadı."),
                _bullet("Not: Yatırım tavsiyesi değildir; manuel karar sende."),
            ]

        edge, bracket, fair, implied = candidate
        reasons = []
        if edge < 0.05:
            reasons.append("edge eşiği altında")
        if boundary == "YÜKSEK":
            reasons.append("sınır riski YÜKSEK")
        if implied <= 0.0 or implied >= 1.0:
            reasons.append("piyasa fiyatı geçersiz")
        should_bet = not reasons

        lines = [
            _bullet(f"Önerilen bracket: {bracket if should_bet else 'BET YOK'}"),
            _bullet(f"En iyi aday{'' if should_bet else ' (işlem yok)'}: {bracket}"),
            _bullet(f"Market fiyat: {_fmt_cents(implied)}"),
            _bullet(f"Bot fair prob: {_fmt_pct(fair)}"),
            _bullet(f"Edge: {_fmt_pp(edge)}"),
            _bullet(f"Sınır riski: {boundary}"),
        ]
        if should_bet:
            lines.append(_bullet(f"Beklenen EV: ${_fmt_num(_expected_profit_usd(100.0, fair, implied))}"))
            lines.append(_bullet("Karar: MANUEL ONAY GEREKİR"))
        else:
            lines.append(_bullet("Beklenen EV: gösterilmiyor (SKIP)"))
            lines.append(_bullet(f"Karar: SKIP ({'; '.join(reasons)})"))
        lines.append(_bullet("Not: Yatırım tavsiyesi değildir; manuel karar sende."))
        return lines

    def _data_quality_lines(
        self,
        target_date: date,
        metar: METARNormalized | None,
        taf: TAFNormalized | None,
        model_bundle: ModelBundle | None,
        market: MarketSnapshot | None,
    ) -> list[str]:
        lines: list[str] = []
        if metar is None:
            lines.append(_bullet("METAR: veri yok"))
        elif metar.observation_time.astimezone(self.tz).date() != target_date:
            local_date = metar.observation_time.astimezone(self.tz).date()
            lines.append(_bullet(f"METAR: güncel, fakat hedef gün değil ({local_date.isoformat()})"))
        elif metar.is_stale:
            lines.append(_bullet(f"METAR: eski ({metar.age_minutes:.0f} dk)"))
        else:
            lines.append(_bullet(f"METAR: güncel ({metar.age_minutes:.0f} dk)"))

        if model_bundle is None:
            lines.append(_bullet("Modeller: veri yok"))
        else:
            available = len(model_bundle.available_forecasts)
            total = len(model_bundle.forecasts)
            lines.append(_bullet(f"Modeller: {available}/{total} kullanılabilir"))

        if taf is None:
            lines.append(_bullet("TAF: veri yok"))
        else:
            risk = "yağış/CB riski var" if taf.rain_or_storm_risk else "belirgin yağış/CB riski yok"
            lines.append(_bullet(f"TAF: var, {risk}"))

        if market is None:
            lines.append(_bullet("Polymarket: ilgili market bulunamadı"))
        elif not market.valid_for_target:
            lines.append(_bullet(f"Polymarket: hedefle uyumsuz ({market.validation_message or 'neden yok'})"))
        else:
            lines.append(_bullet("Polymarket: hedef market doğrulandı"))
        return lines


def _bullet(text: str) -> str:
    return f"• {text}"


def _report_title(label: str | None) -> str:
    lookup = {
        "09:00": "ANKARA ESENBOĞA SABAH TAHMİNİ",
        "12:00": "ANKARA ESENBOĞA ÖĞLE GÜNCELLEMESİ",
        "15:00": "ANKARA ESENBOĞA RİSK GÜNCELLEMESİ",
        "command": "ANKARA ESENBOĞA MANUEL TAHMİN",
        "cli": "ANKARA ESENBOĞA MANUEL TAHMİN",
        "edge": "ANKARA ESENBOĞA EDGE KONTROLÜ",
    }
    return lookup.get(label or "", "ANKARA ESENBOĞA GÜNLÜK MAKSİMUM SICAKLIK TAHMİNİ")


def _display_model_name(model: str) -> str:
    name_map = {
        "icon_eu": "ICON-EU",
        "icon_global": "ICON-Global",
        "visual_crossing": "Visual Crossing",
        "tomorrow_io": "Tomorrow.io",
        "ecmwf": "ECMWF",
        "gfs": "GFS",
        "icon": "ICON",
    }
    return next((display for key, display in name_map.items() if key in model.lower()), model)


def _confidence_label(score: int) -> str:
    if score >= 75:
        return "yüksek"
    if score >= 55:
        return "orta"
    if score >= 35:
        return "düşük"
    return "çok düşük"


def _source_state_label(state: str) -> str:
    return {
        "ok": "çalışıyor",
        "degraded": "kısıtlı",
        "down": "kapalı",
        "unavailable": "veri yok",
    }.get(state, state)


def _fmt_range(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "veri yok"
    return f"{low:.1f}°C - {high:.1f}°C"


def _fmt_c(value: float | None) -> str:
    return f"{value:.1f}°C" if value is not None else "veri yok"


def _fmt_num(value: float | int | None) -> str:
    if value is None:
        return "veri yok"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _fmt_pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "veri yok"


def _fmt_cents(value: float | None) -> str:
    return f"{_fmt_num(value * 100)}¢" if value is not None else "veri yok"


def _fmt_pp(value: float | None) -> str:
    return f"{value * 100:+.1f} pp" if value is not None else "veri yok"


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
        return "veri yok"
    nearest_half_degree_distance = round(abs((analysis.final_tmax_c - 0.5) - round(analysis.final_tmax_c - 0.5)), 3)
    sigma = analysis.probability_sigma_c or 0.0
    if nearest_half_degree_distance <= 0.3 or sigma >= 1.4:
        return "YÜKSEK"
    if nearest_half_degree_distance <= 0.45 or sigma >= 0.9:
        return "ORTA"
    return "DÜŞÜK"


def _expected_profit_usd(stake_usd: float, fair_probability: float, yes_price: float) -> float | None:
    if yes_price <= 0.0 or yes_price >= 1.0:
        return None
    shares = stake_usd / yes_price
    return fair_probability * shares - stake_usd


def _format_clouds(clouds: list[dict]) -> str:
    if not clouds:
        return "veri yok"
    return ", ".join(f"{cloud.get('cover', '?')}{cloud.get('base', '')}" for cloud in clouds)


def _adj(adjustment: object | None) -> str:
    if adjustment is None:
        return "veri yok"
    return f"{adjustment.summary} ({adjustment.value_c:+.1f}°C)"
