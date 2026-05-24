from __future__ import annotations

from datetime import date, datetime
from statistics import mean
from typing import Any
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
        risk = _boundary_risk(analysis)
        return "\n".join(
            [
                "☁️⛅️🌤️🌩️🌥️🌨️🌧️🌦️ Ankara Esenboğa Günün Tahmini",
                "",
                _market_headline(market),
                "",
                f"📅 Market Kapanış: {_market_close_line(market, analysis.target_date, self.tz)}",
                "",
                f"👥 Model + piyasa tahmini: {_consensus_line(analysis, market)}",
                f"⚠️ Risk {_risk_emoji(risk)} {risk}",
                "",
                "🕒 Saatlik Beklentiler",
                "",
                *self._hourly_expectation_lines(model_bundle, analysis.target_date, report_time),
                "",
                f"⚡ Tahmini Sonuç: {_fmt_integer_c(analysis.final_tmax_c)}",
                _tree(f"Ana aralık: {_fmt_range(analysis.main_range_low_c, analysis.main_range_high_c)}"),
                _tree(f"Son METAR: {_decoded_metar(metar)}"),
                _tree(f"Gözlem zamanı: {_observation_time(metar, self.tz)}", last=True),
                "",
                "🤖 Model tahminleri",
                *self._model_digest_lines(model_bundle, analysis, taf),
                "",
                "👉 Meteorolojik Veriler",
                *self._meteorological_digest_lines(analysis, metar, taf, model_bundle),
                "",
                "Hava dinamiği:",
                *self._dynamic_lines(analysis),
                "",
                "☁️ Bulut Aktiviteleri:",
                *self._cloud_digest_lines(analysis, metar, model_bundle, report_time),
                "",
                "🧑‍🏫 Forum/Piyasa Analizi:",
                *self._forum_digest_lines(analysis, market),
                "",
                "🤖 AI Özeti:",
                *self._ai_digest_lines(analysis, market),
                "",
                _live_price_line(analysis, market),
                "",
                f"⏳ Son Güncelleme: {report_time:%H:%M}",
                "⚠️ Yeni değişkenler / son değişiklikler:",
                _tree("Basınç trendi, 850 hPa sıcaklığı, 500 hPa yüksekliği ve CAPE artık tahmin etkisine giriyor."),
                _tree("MGM/Herbie şimdilik kaynak sağlığı ekranında görünür; aktif veri yoksa sahte değer yazılmıyor.", last=True),
                "",
                "Not: Yatırım tavsiyesi değildir; manuel karar sende.",
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
            _bullet(f"Basınç/üst seviye: {_adj(lookup.get('synoptic_pressure'))}"),
            _bullet(f"Bulut/radyasyon: {_adj(lookup.get('cloud_radiation'))}"),
            _bullet(f"Yağış/zemin: {_adj(lookup.get('rain_soil'))}"),
        ]

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

    def _hourly_expectation_lines(
        self,
        model_bundle: ModelBundle | None,
        target_date: date,
        report_time: datetime,
    ) -> list[str]:
        lines: list[str] = []
        hour_values: dict[int, float | None] = {}
        for hour in (9, 11, 13, 15, 17, 19):
            value = _avg_model_field_at_hour(model_bundle, target_date, "temperature_2m_c", hour)
            hour_values[hour] = value
        peak_hour = max(
            (hour for hour, value in hour_values.items() if value is not None),
            key=lambda hour: hour_values[hour] or -999.0,
            default=None,
        )
        for hour, value in hour_values.items():
            note = ""
            if hour == peak_hour and value is not None:
                note = " peak"
            if hour >= 17:
                cloud = _avg_model_field_at_hour(model_bundle, target_date, "cloud_cover_pct", hour)
                if cloud is not None:
                    note = f" · bulut %{cloud:.0f}" if not note else f"{note}, bulut %{cloud:.0f}"
            lines.append(_tree(f"{hour:02d}:00 → {_fmt_c(value)}{note}"))

        momentum = _temperature_momentum(model_bundle, target_date, report_time)
        lines.append(_tree("Sıcaklık Momentum"))
        if momentum is None:
            lines.append("│  Son 90 dk: canlı trend için geçmiş METAR yok")
            lines.append("│  → Model momentum verisi yok")
        else:
            label = "güçlü momentum" if abs(momentum) >= 1.5 else "ılımlı momentum"
            arrow = "↗" if momentum > 0 else "↘" if momentum < 0 else "→"
            lines.append(f"│  Son 90 dk/model: {momentum:+.1f}°C")
            lines.append(f"└ {arrow} {label}")
        return lines

    def _model_digest_lines(
        self,
        model_bundle: ModelBundle | None,
        analysis: ForecastAnalysis,
        taf: TAFNormalized | None,
    ) -> list[str]:
        if model_bundle is None:
            return [
                _tree("Model verisi yok"),
                _tree("AviationWeather/TAF: veri yok", last=True),
            ]
        lines: list[str] = []
        for forecast in model_bundle.forecasts:
            label = _display_model_name(forecast.model)
            link = _model_link(forecast.model, self.settings)
            value = _fmt_c(forecast.tmax_c) if forecast.available else "unavailable"
            weight = analysis.model_weights.get(forecast.model)
            weight_text = f" · ağırlık %{weight * 100:.0f}" if weight is not None else ""
            link_text = f" · {link}" if link else ""
            lines.append(_tree(f"{label}: {value}{weight_text}{link_text}"))
        taf_text = "yağış/CB riski var" if taf and taf.rain_or_storm_risk else "belirgin CB riski yok" if taf else "veri yok"
        lines.append(_tree(f"AviationWeather/TAF: {taf_text} · https://aviationweather.gov/data/metar/?id={self.settings.ltac_icao}"))
        values = [forecast.tmax_c for forecast in model_bundle.available_forecasts if forecast.tmax_c is not None]
        if values:
            lines.append(_tree(f"Model aralığı: {min(values):.1f}°C - {max(values):.1f}°C", last=True))
        else:
            lines.append(_tree("Model aralığı: veri yok", last=True))
        return lines

    def _meteorological_digest_lines(
        self,
        analysis: ForecastAnalysis,
        metar: METARNormalized | None,
        taf: TAFNormalized | None,
        model_bundle: ModelBundle | None,
    ) -> list[str]:
        synoptic = _adjustment(analysis, "synoptic_pressure")
        advection = _adjustment(analysis, "advection")
        rain = _adjustment(analysis, "rain_soil")
        pressure = metar.pressure_hpa if metar else _adjustment_input(synoptic, "midday_pressure_msl_hpa")
        pressure_trend = _adjustment_input(synoptic, "pressure_trend_hpa")
        cape = _max_model_field_between(model_bundle, analysis.target_date, "cape_jkg", 10, 16)
        precip_total = _avg_daily_precip(model_bundle, analysis.target_date)
        precip_probability = _precip_probability(model_bundle, analysis.target_date)
        temp_850 = _mean_model_field_between(model_bundle, analysis.target_date, "temperature_850hpa_c", 6, 14)
        fog_line = _fog_inversion_line(metar, temp_850, analysis.target_date)
        has_taf_rain = bool(taf and taf.rain_or_storm_risk and rain is not None)
        lines = [
            _tree(f"Canlı sıcaklık: {_fmt_c(metar.temperature_c if metar else None)}"),
            _tree(f"Yağış olasılığı: {_fmt_pct(precip_probability)}"),
            _tree(f"Yağış milimetresi: {_fmt_num(precip_total)} mm"),
            _tree(f"Çiğ noktası: {_fmt_c(metar.dew_point_c if metar else None)}"),
            _tree(f"Nem: %{metar.relative_humidity if metar and metar.relative_humidity is not None else 'veri yok'}"),
            _tree("LI/TT: veri yok; bu iki kararsızlık indisi aktif sondaj/GRIB entegrasyonu gelince hesaplanacak."),
            _tree(f"Radyasyon sisi/enversiyon: {fog_line}"),
            _tree(f"Topografik rüzgârlar: {_topographic_wind_line(metar)}"),
            _tree("CIN: veri yok; konvektif engelleme için sondaj tabanlı hesap ayrı entegrasyon gerektiriyor."),
            _tree(f"CAPE: {_fmt_num(cape)} J/kg; {_cape_line(cape)}"),
            _tree(f"Rüzgâr: {_wind_line(metar)}"),
            _tree(f"Basınç: {_fmt_num(pressure)} hPa; {_pressure_line(pressure, pressure_trend)}"),
            _tree(f"Sıcaklık adveksiyonu: {_adj(advection)}", last=not has_taf_rain),
        ]
        if has_taf_rain:
            lines.append(_tree(f"TAF yağış/CB teyidi: {_adj(rain)}", last=True))
        return lines

    def _cloud_digest_lines(
        self,
        analysis: ForecastAnalysis,
        metar: METARNormalized | None,
        model_bundle: ModelBundle | None,
        report_time: datetime,
    ) -> list[str]:
        cloud = _adjustment(analysis, "cloud_radiation")
        low = _adjustment_input(cloud, "low_cloud_mean_pct")
        mid = _adjustment_input(cloud, "mid_cloud_mean_pct")
        high = _adjustment_input(cloud, "high_cloud_mean_pct")
        cloud_cover = _mean_model_field_between(model_bundle, analysis.target_date, "cloud_cover_pct", 10, 16)
        precip_total = _avg_daily_precip(model_bundle, analysis.target_date)
        lines = [
            _tree(f"Yağmur yüklü bulutlar: {_rain_cloud_line(precip_total, cloud_cover)}"),
            _tree(f"Bulut yönü: {_cloud_direction(model_bundle, analysis.target_date, metar)}"),
            _tree(f"Bulut sayısı/kapalılık: {_fmt_pct_fraction(cloud_cover)}"),
            _tree(f"Tahmini açılma zamanı: {_clearing_time(model_bundle, analysis.target_date, report_time)}"),
            _tree(f"Alçak/orta/yüksek bulutlar: alçak {_fmt_pct_fraction(low)}, orta {_fmt_pct_fraction(mid)}, yüksek {_fmt_pct_fraction(high)}", last=True),
        ]
        return lines

    def _forum_digest_lines(self, analysis: ForecastAnalysis, market: MarketSnapshot | None) -> list[str]:
        candidate = _best_market_candidate(analysis, market)
        lines = [
            _tree("Forum entegrasyonu yok; sahte kullanıcı yorumu üretilmedi."),
        ]
        if candidate is None:
            lines.append(_tree("Piyasa sinyali: pozitif edge bulunamadı.", last=True))
        else:
            edge, bracket, fair, implied = candidate
            lines.append(_tree(f"Piyasa sinyali: {bracket} için fair {_fmt_pct(fair)}, fiyat {_fmt_cents(implied)}, edge {_fmt_pp(edge)}.", last=True))
        return lines

    def _ai_digest_lines(self, analysis: ForecastAnalysis, market: MarketSnapshot | None) -> list[str]:
        candidate = _best_market_candidate(analysis, market)
        lines = [
            f"Ana senaryo: Esenboğa maksimumu {_fmt_c(analysis.final_tmax_c)} civarı; güven {analysis.confidence_score}/100.",
            f"Yukarı risk: {analysis.risks.get('upward', 'veri yok')}",
            f"Aşağı risk: {analysis.risks.get('downward', 'veri yok')}",
            f"Kritik belirsizlik: {analysis.risks.get('critical', 'veri yok')}",
        ]
        if candidate is None:
            lines.append("Net karar: geçerli fiyat/fair probability ile agresif bahis sinyali yok.")
        else:
            edge, bracket, fair, implied = candidate
            boundary = _boundary_risk(analysis)
            decision = "manuel onay gerektirir" if edge >= 0.05 and boundary != "YÜKSEK" and 0.0 < implied < 1.0 else "BET YOK / izle"
            lines.append(f"Net karar: {bracket} adayı {decision}; fair {_fmt_pct(fair)}, fiyat {_fmt_cents(implied)}, edge {_fmt_pp(edge)}.")
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


def _tree(text: str, *, last: bool = False) -> str:
    return f"{'└' if last else '├'} {text}"


def _market_headline(market: MarketSnapshot | None) -> str:
    if market is None:
        return "Highest temperature in Ankara · Vol: veri yok"
    title = market.title or "Highest temperature in Ankara"
    volume = f"${_fmt_num(market.volume)}"
    return f"{title} · Vol: {volume} ({market.link})"


def _market_close_line(market: MarketSnapshot | None, target_date: date, tz: ZoneInfo) -> str:
    raw = market.raw_json if market else {}
    end_value = raw.get("endDate") or raw.get("endDateIso") or raw.get("closeTime") if isinstance(raw, dict) else None
    if end_value:
        parsed = _parse_dt(end_value)
        if parsed is not None:
            return f"{parsed.astimezone(tz):%b %-d %H:%M} Türkiye saati"
    return f"{target_date:%b %-d} 23:59 Türkiye saati"


def _consensus_line(analysis: ForecastAnalysis, market: MarketSnapshot | None) -> str:
    model_pick = _fmt_integer_c(analysis.final_tmax_c)
    candidate = _best_market_candidate(analysis, market)
    if candidate is None:
        return model_pick
    _, bracket, fair, _ = candidate
    return f"{model_pick} · en güçlü piyasa/fair adayı {bracket} ({_fmt_pct(fair)})"


def _risk_emoji(risk: str) -> str:
    return {"DÜŞÜK": "🟢", "ORTA": "🟡", "YÜKSEK": "🟠"}.get(risk, "⚪")


def _fmt_integer_c(value: float | None) -> str:
    if value is None:
        return "veri yok"
    return f"{round(value):.0f}°C"


def _fmt_pct_fraction(value: float | None) -> str:
    if value is None:
        return "veri yok"
    return f"%{value:.0f}"


def _observation_time(metar: METARNormalized | None, tz: ZoneInfo) -> str:
    if metar is None:
        return "veri yok"
    return f"{metar.observation_time.astimezone(tz):%H:%M}"


def _decoded_metar(metar: METARNormalized | None) -> str:
    if metar is None:
        return "veri yok"
    parts = [
        f"{metar.temperature_c:.1f}°C",
        f"çiy {metar.dew_point_c:.1f}°C",
        f"rüzgâr {metar.wind_direction_deg if metar.wind_direction_deg is not None else 'VRB'}°/{metar.wind_speed_kt:.0f} kt",
        f"basınç {_fmt_num(metar.pressure_hpa)} hPa",
        f"bulut {_format_clouds(metar.cloud_layers)}",
    ]
    if metar.visibility_m is not None:
        parts.append(f"görüş {metar.visibility_m} m")
    return ", ".join(parts)


def _adjustment(analysis: ForecastAnalysis, name: str) -> Any | None:
    return next((item for item in analysis.adjustments if item.name == name), None)


def _adjustment_input(adjustment: Any | None, key: str) -> float | None:
    if adjustment is None:
        return None
    value = adjustment.inputs.get(key)
    return float(value) if value is not None else None


def _model_link(model: str, settings: Settings) -> str | None:
    lowered = model.lower()
    lat = settings.ltac_latitude
    lon = settings.ltac_longitude
    if any(key in lowered for key in ("icon", "ecmwf", "gfs")):
        return f"https://open-meteo.com/en/docs?latitude={lat}&longitude={lon}"
    if "visual" in lowered:
        return "https://www.visualcrossing.com/weather/weather-data-services"
    if "tomorrow" in lowered:
        return "https://www.tomorrow.io/weather-api/"
    return None


def _avg_model_field_at_hour(
    model_bundle: ModelBundle | None,
    target_date: date,
    field: str,
    hour: int,
) -> float | None:
    values = []
    if model_bundle is None:
        return None
    for forecast in model_bundle.available_forecasts:
        for point in forecast.hourly:
            if point.time.date() == target_date and point.time.hour == hour:
                value = getattr(point, field, None)
                if value is not None:
                    values.append(float(value))
    return mean(values) if values else None


def _mean_model_field_between(
    model_bundle: ModelBundle | None,
    target_date: date,
    field: str,
    start_hour: int,
    end_hour: int,
) -> float | None:
    values = []
    if model_bundle is None:
        return None
    for forecast in model_bundle.available_forecasts:
        for point in forecast.hourly:
            if point.time.date() == target_date and start_hour <= point.time.hour <= end_hour:
                value = getattr(point, field, None)
                if value is not None:
                    values.append(float(value))
    return mean(values) if values else None


def _max_model_field_between(
    model_bundle: ModelBundle | None,
    target_date: date,
    field: str,
    start_hour: int,
    end_hour: int,
) -> float | None:
    values = []
    if model_bundle is None:
        return None
    for forecast in model_bundle.available_forecasts:
        for point in forecast.hourly:
            if point.time.date() == target_date and start_hour <= point.time.hour <= end_hour:
                value = getattr(point, field, None)
                if value is not None:
                    values.append(float(value))
    return max(values) if values else None


def _avg_daily_precip(model_bundle: ModelBundle | None, target_date: date) -> float | None:
    totals = []
    if model_bundle is None:
        return None
    for forecast in model_bundle.available_forecasts:
        values = [
            float(point.precipitation_mm)
            for point in forecast.hourly
            if point.time.date() == target_date and point.precipitation_mm is not None
        ]
        if values:
            totals.append(sum(values))
    return mean(totals) if totals else None


def _precip_probability(model_bundle: ModelBundle | None, target_date: date) -> float | None:
    if model_bundle is None or not model_bundle.available_forecasts:
        return None
    wet = 0
    usable = 0
    for forecast in model_bundle.available_forecasts:
        values = [
            float(point.precipitation_mm)
            for point in forecast.hourly
            if point.time.date() == target_date and point.precipitation_mm is not None
        ]
        if not values:
            continue
        usable += 1
        if sum(values) >= 0.2:
            wet += 1
    return wet / usable if usable else None


def _temperature_momentum(
    model_bundle: ModelBundle | None,
    target_date: date,
    report_time: datetime,
) -> float | None:
    current_hour = report_time.hour
    previous_hour = current_hour - 2
    if previous_hour < 0:
        return None
    current = _avg_model_field_at_hour(model_bundle, target_date, "temperature_2m_c", current_hour)
    previous = _avg_model_field_at_hour(model_bundle, target_date, "temperature_2m_c", previous_hour)
    if current is None or previous is None:
        return None
    return current - previous


def _fog_inversion_line(metar: METARNormalized | None, temp_850: float | None, target_date: date) -> str:
    if metar is None or temp_850 is None:
        return "veri yok; 850 hPa ile yüzey farkı hesaplanamadı"
    delta = metar.temperature_c - temp_850
    cold_season = target_date.month in {10, 11, 12, 1, 2, 3}
    if cold_season and delta < 1.0 and metar.relative_humidity is not None and metar.relative_humidity >= 85:
        return f"yüksek risk; yüzey-850 hPa farkı {delta:+.1f}°C ve nem %{metar.relative_humidity}"
    if delta < 0.0:
        return f"enversiyon işareti var; yüzey 850 hPa'dan {abs(delta):.1f}°C serin"
    return f"belirgin sis/enversiyon sinyali düşük; yüzey-850 hPa farkı {delta:+.1f}°C"


def _topographic_wind_line(metar: METARNormalized | None) -> str:
    if metar is None:
        return "veri yok"
    if metar.wind_speed_kt <= 4:
        return "zayıf rüzgâr; gece drenaj/katabatik akışa açık"
    if metar.wind_gust_kt and metar.wind_gust_kt - metar.wind_speed_kt >= 8:
        return f"hamleli akış; {metar.wind_speed_kt:.0f} kt ortalama, {metar.wind_gust_kt:.0f} kt hamle"
    return f"{metar.wind_direction_deg if metar.wind_direction_deg is not None else 'VRB'}° yönlü {metar.wind_speed_kt:.0f} kt akış; ani yön değişimi için METAR/TAF takip"


def _cape_line(cape: float | None) -> str:
    if cape is None:
        return "konvektif enerji verisi yok"
    if cape >= 1000:
        return "kuvvetli konveksiyon/CB riski artmış"
    if cape >= 500:
        return "orta seviye konveksiyon potansiyeli var"
    if cape >= 100:
        return "zayıf konveksiyon potansiyeli"
    return "konvektif enerji düşük"


def _wind_line(metar: METARNormalized | None) -> str:
    if metar is None:
        return "veri yok"
    direction = metar.wind_direction_deg if metar.wind_direction_deg is not None else "VRB"
    gust = f", hamle {metar.wind_gust_kt:.0f} kt" if metar.wind_gust_kt else ""
    return f"{direction}° / {metar.wind_speed_kt:.0f} kt{gust}; {_topographic_wind_line(metar)}"


def _pressure_line(pressure: float | None, trend: float | None) -> str:
    if pressure is None and trend is None:
        return "basınç etkisi için veri yok"
    parts = []
    if pressure is not None:
        if pressure >= 1018:
            parts.append("yüksek basınç ısınmayı/güneşlenmeyi destekleyebilir")
        elif pressure <= 1008:
            parts.append("alçak basınç bulut/yağış riskini artırabilir")
        else:
            parts.append("basınç nötr bantta")
    if trend is not None:
        if trend <= -2:
            parts.append(f"düşüş {trend:+.1f} hPa, konveksiyon/cephe riski artar")
        elif trend >= 2:
            parts.append(f"yükseliş {trend:+.1f} hPa, stabilizasyon sinyali")
        else:
            parts.append(f"trend zayıf ({trend:+.1f} hPa)")
    return "; ".join(parts)


def _rain_cloud_line(precip_total: float | None, cloud_cover: float | None) -> str:
    if precip_total is None and cloud_cover is None:
        return "veri yok"
    if precip_total is not None and precip_total >= 5:
        return "yağış yüklü bulut riski belirgin"
    if cloud_cover is not None and cloud_cover >= 70:
        return "kapalı bulut var ama yağış sinyali ayrıca kontrol edilmeli"
    return "belirgin yağmur yüklü bulut sinyali düşük"


def _cloud_direction(
    model_bundle: ModelBundle | None,
    target_date: date,
    metar: METARNormalized | None,
) -> str:
    direction = _mean_model_field_between(model_bundle, target_date, "wind_direction_10m_deg", 10, 16)
    speed = _mean_model_field_between(model_bundle, target_date, "wind_speed_10m_kt", 10, 16)
    if direction is None and metar is not None:
        direction = float(metar.wind_direction_deg) if metar.wind_direction_deg is not None else None
        speed = metar.wind_speed_kt
    if direction is None:
        return "veri yok"
    return f"{direction:.0f}° yönlü akış, yaklaşık {_fmt_num(speed)} kt"


def _clearing_time(model_bundle: ModelBundle | None, target_date: date, report_time: datetime) -> str:
    if model_bundle is None:
        return "veri yok"
    for hour in range(max(report_time.hour, 6), 22):
        cover = _avg_model_field_at_hour(model_bundle, target_date, "cloud_cover_pct", hour)
        precip = _avg_model_field_at_hour(model_bundle, target_date, "precipitation_mm", hour)
        if cover is not None and cover <= 45 and (precip is None or precip < 0.2):
            return f"{hour:02d}:00 sonrası açılma olası"
    return "bugün belirgin açılma penceresi yok"


def _live_price_line(analysis: ForecastAnalysis, market: MarketSnapshot | None) -> str:
    candidate = _best_market_candidate(analysis, market)
    if candidate is None:
        return "💵 Polymarket Canlı Fiyat: veri yok"
    _, bracket, _, implied = candidate
    trend = _trade_trend(market, bracket)
    suffix = f" ({trend})" if trend else ""
    return f"💵 Polymarket Canlı Fiyat: {bracket} {_fmt_cents(implied)}{suffix}"


def _trade_trend(market: MarketSnapshot | None, bracket: str) -> str | None:
    if market is None:
        return None
    outcome = next((item for item in market.outcomes if item.bracket == bracket), None)
    if outcome is None or len(outcome.recent_trades) < 2:
        return None
    prices = [float(trade["price"]) for trade in outcome.recent_trades if trade.get("price") is not None]
    if len(prices) < 2:
        return None
    delta = prices[0] - prices[-1]
    if abs(delta) < 0.005:
        return "son işlemlerde yatay"
    return f"son işlemlerde {delta * 100:+.1f}¢"


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed
