from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.schemas import (
    ActualResult,
    ForecastAnalysis,
    ForumAnalysis,
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
        forum: ForumAnalysis | None = None,
        report_label: str | None = None,
        previous_analysis: ForecastAnalysis | None = None,
        previous_model_tmax_c: Mapping[str, float | None] | None = None,
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
                _bullet(
                    f"Beklenen maksimum: {_fmt_c_with_trend(analysis.final_tmax_c, previous_analysis.final_tmax_c if previous_analysis else None)}"
                ),
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
                *self._model_lines(model_bundle, analysis, previous_model_tmax_c),
                "",
                "Hava dinamiği:",
                *self._dynamic_lines(analysis),
                "",
                "Neden bu tahmin?",
                *self._rationale_lines(analysis),
                "",
                "Forum analizi:",
                *self._forum_lines(forum),
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

    def aviation_report(
        self,
        *,
        analysis: ForecastAnalysis,
        metar: METARNormalized | None,
        taf: TAFNormalized | None,
        model_bundle: ModelBundle | None,
        market: MarketSnapshot | None,
        wunderground_result: ActualResult | None,
        intraday_result: ActualResult | None,
        wunderground_url: str,
        now: datetime | None = None,
    ) -> str:
        report_time = (now or datetime.now(timezone.utc)).astimezone(self.tz)
        return "\n".join(
            [
                "LTAC HAVACILIK + WUNDERGROUND BRİFİNGİ",
                "",
                f"Tarih: {analysis.target_date.isoformat()}",
                f"Rapor saati: {report_time:%H:%M} ({self.settings.report_timezone})",
                "Meydan: Ankara Esenboğa / LTAC",
                "",
                "Wunderground settlement:",
                *self._wunderground_settlement_lines(
                    analysis.target_date,
                    metar,
                    wunderground_result,
                    intraday_result,
                    wunderground_url,
                    report_time,
                ),
                "",
                "Havacılık gözlem/tahmin sinyalleri:",
                *self._aviation_watch_lines(metar, taf),
                "",
                "Pik sıcaklık dinamiği:",
                *self._peak_temperature_lines(model_bundle, taf),
                "",
                "Polymarket karar çerçevesi:",
                *self._settlement_market_lines(analysis, market),
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

    def models_report(self, bundle: ModelBundle | None, previous_model_tmax_c: Mapping[str, float | None] | None = None) -> str:
        if bundle is None:
            return "Model verisi yok."
        return "\n".join(["MODEL KARŞILAŞTIRMA", *self._model_lines(bundle, previous_model_tmax_c=previous_model_tmax_c)])

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

    def forum_report(self, forum: ForumAnalysis | None) -> str:
        return "\n".join(["HAVAFORUM ANKARA ANALİZİ", *self._forum_lines(forum, include_examples=True)])

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

    def _model_lines(
        self,
        bundle: ModelBundle | None,
        analysis: ForecastAnalysis | None = None,
        previous_model_tmax_c: Mapping[str, float | None] | None = None,
    ) -> list[str]:
        if bundle is None:
            return [_bullet("ECMWF: veri yok"), _bullet("GFS: veri yok"), _bullet("ICON: veri yok"), _bullet("Model aralığı: veri yok")]
        lines = []
        for forecast in bundle.forecasts:
            label = _display_model_name(forecast.model)
            weight = ""
            if analysis and forecast.model in analysis.model_weights:
                weight = f" (ağırlık %{analysis.model_weights[forecast.model] * 100:.0f})"
            previous_tmax = previous_model_tmax_c.get(forecast.model) if previous_model_tmax_c else None
            value = _fmt_c_with_trend(forecast.tmax_c, previous_tmax) if forecast.available else "veri yok"
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

    def _forum_lines(self, forum: ForumAnalysis | None, *, include_examples: bool = False) -> list[str]:
        if forum is None:
            return [_bullet("HavaForum: veri yok")]
        if forum.unavailable_reason:
            return [_bullet(f"HavaForum: {forum.unavailable_reason}")]
        lines = [
            _bullet(f"Özet: {forum.summary}"),
            _bullet(f"Mesaj kapsamı: {forum.same_day_post_count} aynı gün, {forum.previous_day_tomorrow_post_count} önceki gün 'yarın' bağlamı"),
        ]
        if forum.latest_post_at:
            lines.append(_bullet(f"Son forum mesajı: {forum.latest_post_at.astimezone(self.tz):%Y-%m-%d %H:%M}"))
        if forum.locations:
            lines.append(_bullet(f"Öne çıkan bölgeler: {', '.join(forum.locations[:5])}"))
        if forum.signals:
            signals = ", ".join(f"{name} {count}" for name, count in list(forum.signals.items())[:4])
            lines.append(_bullet(f"Sinyaller: {signals}"))
        if include_examples:
            for post in forum.posts[-3:]:
                text = " ".join(post.text.split())
                if len(text) > 180:
                    text = f"{text[:177]}..."
                author = f"{post.author}: " if post.author else ""
                lines.append(_bullet(f"{post.published_at.astimezone(self.tz):%H:%M} {author}{text}"))
        return lines

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

    def _wunderground_settlement_lines(
        self,
        target_date: date,
        metar: METARNormalized | None,
        wunderground_result: ActualResult | None,
        intraday_result: ActualResult | None,
        wunderground_url: str,
        report_time: datetime,
    ) -> list[str]:
        lines = [
            _bullet(f"History URL: {wunderground_url}"),
            _bullet("Kural: Wunderground LTAC History tablosundaki METAR kaynaklı tam °C değeri esas alınır; gün içinde tek 21°C raporu settlement tavanını 21°C yapar."),
        ]
        if wunderground_result and wunderground_result.rounded_tmax_c is not None:
            lines.append(
                _bullet(
                    f"Wunderground final: {wunderground_result.rounded_tmax_c}°C "
                    f"(ham {_fmt_c(wunderground_result.tmax_c)})"
                )
            )
        elif wunderground_result and wunderground_result.manual_required:
            lines.append(_bullet("Wunderground final: statik sayfa final Tmax vermedi; History ekranından veya /result ile manuel doğrulama gerekir."))
        else:
            lines.append(_bullet("Wunderground final: hedef gün için henüz okunabilir final yok."))

        if intraday_result and intraday_result.rounded_tmax_c is not None:
            observation_count = intraday_result.raw_payload.get("observation_count")
            peak_time = intraday_result.raw_payload.get("max_observation_time")
            suffix = []
            if peak_time:
                suffix.append(f"pik {peak_time}")
            if observation_count:
                suffix.append(f"{observation_count} METAR kaydı")
            detail = f" ({', '.join(suffix)})" if suffix else ""
            lines.append(
                _bullet(
                    f"Canlı ASOS/METAR proxy: {intraday_result.rounded_tmax_c}°C "
                    f"(ham max {_fmt_c(intraday_result.tmax_c)}){detail}"
                )
            )
        else:
            lines.append(_bullet("Canlı ASOS/METAR proxy: veri yok"))

        if metar and metar.observation_time.astimezone(self.tz).date() == target_date:
            metar_integer = _settlement_integer_from_reported_temp(metar.temperature_c)
            current_floor = max(
                value
                for value in (metar_integer, intraday_result.rounded_tmax_c if intraday_result else None)
                if value is not None
            )
            lines.append(_bullet(f"Son METAR sıcaklığı: {metar_integer}°C; canlı proxy settlement tavanı en az {current_floor}°C."))
        elif metar:
            local_date = metar.observation_time.astimezone(self.tz).date()
            lines.append(_bullet(f"Son METAR hedef gün değil ({local_date.isoformat()}); canlı tavan çıkarımı için kullanılmadı."))
        else:
            lines.append(_bullet("Son METAR sıcaklığı: veri yok"))

        lines.extend(
            [
                _bullet(f"Kesinleşme: {_finalization_status(target_date, report_time)}"),
                _bullet("MGM notu: MGM'nin küsuratlı/yuvarlanmış istasyon değeri Wunderground History settlement yerine geçmez."),
            ]
        )
        return lines

    def _aviation_watch_lines(self, metar: METARNormalized | None, taf: TAFNormalized | None) -> list[str]:
        lines: list[str] = []
        if metar:
            spread = metar.temperature_c - metar.dew_point_c
            ceiling = _lowest_cloud_base(metar.cloud_layers)
            ceiling_text = f"{ceiling} ft" if ceiling is not None else "veri yok"
            lines.extend(
                [
                    _bullet(f"METAR: {metar.raw_text}"),
                    _bullet(
                        f"Rüzgâr/görüş/tavan: {metar.wind_direction_deg or 'VRB'}°/{metar.wind_speed_kt:.0f} kt, "
                        f"{metar.visibility_m if metar.visibility_m is not None else 'veri yok'} m, tavan {ceiling_text}"
                    ),
                    _bullet(f"Sıcaklık-işba farkı: {spread:.1f}°C; düşük fark sabah radyasyon sisi/RVR gecikmesi riskini artırır."),
                ]
            )
        else:
            lines.extend([_bullet("METAR: veri yok"), _bullet("Rüzgâr/görüş/tavan: veri yok")])

        if taf:
            changes = [period.change for period in taf.periods if period.change]
            lines.append(_bullet(f"TAF yayın: {taf.issue_time.astimezone(self.tz):%Y-%m-%d %H:%M}; değişim kodları: {', '.join(changes[:5]) if changes else 'BASE'}"))
            lines.append(_bullet(f"TAF konveksiyon/yağış: {_taf_hazard_summary(taf)}"))
        else:
            lines.extend([_bullet("TAF yayın: veri yok"), _bullet("TAF konveksiyon/yağış: veri yok")])
        return lines

    def _peak_temperature_lines(self, model_bundle: ModelBundle | None, taf: TAFNormalized | None) -> list[str]:
        if model_bundle is None:
            return [
                _bullet("Pik pencere: LTAC için normal operasyonel odak 13:30-15:30 lokal; model verisi yok."),
                _bullet("Konveksiyon/radyasyon: veri yok"),
                _bullet("850 hPa / adveksiyon: veri yok"),
            ]

        peaks = []
        heat_points = []
        for forecast in model_bundle.available_forecasts:
            temp_points = [point for point in forecast.hourly if point.temperature_2m_c is not None]
            if temp_points:
                peak = max(temp_points, key=lambda point: point.temperature_2m_c or -999.0)
                peaks.append(
                    f"{_display_model_name(forecast.model)} {_fmt_c(peak.temperature_2m_c)} @{peak.time.astimezone(self.tz):%H:%M}"
                )
            for point in forecast.hourly:
                local_hour = point.time.astimezone(self.tz).hour
                if 11 <= local_hour <= 17:
                    heat_points.append(point)

        cape_values = [point.cape_jkg for point in heat_points if point.cape_jkg is not None]
        cloud_values = [point.cloud_cover_pct for point in heat_points if point.cloud_cover_pct is not None]
        radiation_values = [point.shortwave_radiation_wm2 for point in heat_points if point.shortwave_radiation_wm2 is not None]
        precip_values = [point.precipitation_mm for point in heat_points if point.precipitation_mm is not None]
        temp_850_values = [point.temperature_850hpa_c for point in heat_points if point.temperature_850hpa_c is not None]

        convection = []
        if cape_values:
            convection.append(f"CAPE max {_fmt_num(max(cape_values))} J/kg")
        if taf and taf.rain_or_storm_risk:
            convection.append("TAF CB/TS/SHRA riski var")
        if precip_values:
            convection.append(f"max yağış {_fmt_num(max(precip_values))} mm/saat")

        radiation = []
        if radiation_values:
            radiation.append(f"kısa dalga max {_fmt_num(max(radiation_values))} W/m²")
        if cloud_values:
            radiation.append(f"ortalama bulut %{_fmt_num(mean(cloud_values))}")

        lines = [
            _bullet("Pik pencere: 13:30-15:30 lokal kritik; bu aralıkta CB/bulut patlaması tavanı 1°C aşağı çekebilir."),
            _bullet(f"Model pikleri: {'; '.join(peaks[:5]) if peaks else 'veri yok'}"),
            _bullet(f"Konveksiyon/radyasyon: {'; '.join(convection + radiation) if convection or radiation else 'veri yok'}"),
        ]
        if temp_850_values:
            lines.append(_bullet(f"850 hPa termal seviye: ortalama {_fmt_c(mean(temp_850_values))}; yüzey tavanı/adveksiyon kontrolünde izlenir."))
        else:
            lines.append(_bullet("850 hPa termal seviye: veri yok"))
        return lines

    def _settlement_market_lines(self, analysis: ForecastAnalysis, market: MarketSnapshot | None) -> list[str]:
        settlement_candidate = _nearest_settlement_integer(analysis.final_tmax_c)
        if settlement_candidate is None:
            lines = [_bullet("Bot settlement adayı: veri yok")]
        else:
            lines = [
                _bullet(
                    f"Bot settlement adayı: {settlement_candidate}°C "
                    f"(sürekli tahmin {_fmt_c(analysis.final_tmax_c)}, sınır riski {_boundary_risk(analysis)})"
                )
            ]
        lines.append(_bullet(f"Karar: {analysis.verdict}"))
        candidate = _best_market_candidate(analysis, market)
        if candidate is None:
            lines.append(_bullet("Pozitif edge: geçerli market/fair probability yok."))
        else:
            edge, bracket, fair, implied = candidate
            if implied <= 0.0 or implied >= 1.0:
                lines.append(_bullet(f"En iyi market adayı: {bracket}; piyasa fiyatı geçersiz/likidite yok."))
            elif edge >= 0.05:
                lines.append(_bullet(f"En iyi market adayı: {bracket}; fair {_fmt_pct(fair)}, market {_fmt_cents(implied)}, edge {_fmt_pp(edge)}"))
            else:
                lines.append(_bullet(f"En iyi market adayı: {bracket}; edge {_fmt_pp(edge)} eşiğin altında."))
        lines.append(_bullet("Not: Yatırım tavsiyesi değildir; Wunderground final kesinleşmeden panik işlem yapma."))
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


def _fmt_c_with_trend(value: float | None, previous: float | None) -> str:
    text = _fmt_c(value)
    if value is None or previous is None:
        return text
    delta = value - previous
    if delta > 0.05:
        return f"{text} 🔺 {delta:+.1f}°C"
    if delta < -0.05:
        return f"{text} 🔻 {delta:+.1f}°C"
    return text


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


def _settlement_integer_from_reported_temp(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def _nearest_settlement_integer(value: float | None) -> int | None:
    if value is None:
        return None
    return _settlement_integer_from_reported_temp(value)


def _finalization_status(target_date: date, report_time: datetime) -> str:
    next_utc_midnight = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    if report_time.astimezone(timezone.utc) < next_utc_midnight:
        return "final değil; Wunderground gece/UTC sonrası eksik-hatalı saatleri düzeltebilir"
    return "UTC gün kapanmış; Wunderground History final satırı yine de manuel teyit edilmeli"


def _lowest_cloud_base(clouds: list[dict]) -> int | None:
    bases = []
    for cloud in clouds:
        cover = str(cloud.get("cover") or cloud.get("type") or "")
        if cover in {"CLR", "SKC", "NSC", "NCD"}:
            continue
        base = cloud.get("base")
        if base in (None, ""):
            continue
        try:
            bases.append(int(float(base)))
        except (TypeError, ValueError):
            continue
    return min(bases) if bases else None


def _taf_hazard_summary(taf: TAFNormalized) -> str:
    hazards = []
    for period in taf.periods:
        weather = period.weather or ""
        cloud_types = [str(cloud.get("type") or cloud.get("cover") or "") for cloud in period.clouds]
        if any(token in weather for token in ("TS", "TSRA", "SHRA", "RA")):
            start = period.time_from.strftime("%d %H:%M")
            hazards.append(f"{period.change or 'BASE'} {weather} @{start}")
        if any("CB" in cloud for cloud in cloud_types):
            start = period.time_from.strftime("%d %H:%M")
            hazards.append(f"{period.change or 'BASE'} CB @{start}")
    return "; ".join(hazards[:4]) if hazards else "belirgin TS/CB/SHRA sinyali yok"


def _format_clouds(clouds: list[dict]) -> str:
    if not clouds:
        return "veri yok"
    return ", ".join(f"{cloud.get('cover', '?')}{cloud.get('base', '')}" for cloud in clouds)


def _adj(adjustment: object | None) -> str:
    if adjustment is None:
        return "veri yok"
    return f"{adjustment.summary} ({adjustment.value_c:+.1f}°C)"
