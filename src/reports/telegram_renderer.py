from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from html import escape
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
        recent_observations: list[dict[str, Any]] | None = None,
        report_label: str | None = None,
        previous_analysis: ForecastAnalysis | None = None,
        previous_model_tmax_c: Mapping[str, float | None] | None = None,
        temperature_momentum: tuple[float, int] | None = None,
    ) -> str:
        report_time = analysis.generated_at.astimezone(self.tz)
        boundary = _boundary_risk(analysis)
        return "\n".join(
            [
                f"{_weather_emoji(model_bundle, taf)} Ankara Esenboğa Günün Tahmini",
                "",
                self._market_headline(market),
                "",
                f"📅 Market Kapanış: {self._market_close_text(market)}",
                "",
                f"👥 Bot Tahmini: {_fmt_c_with_trend(analysis.final_tmax_c, previous_analysis.final_tmax_c if previous_analysis else None)}",
                f"⚠️ Risk {_risk_emoji(boundary)} {boundary}",
                "",
                "🕒 Saatlik Beklentiler",
                *self._hourly_expectation_lines(model_bundle, temperature_momentum),
                "",
                f"⚡ Tahmini Sonuç: {_fmt_integer_c(analysis.final_tmax_c)}",
                f"├ Ana aralık: {_fmt_range(analysis.main_range_low_c, analysis.main_range_high_c)}",
                f"├ Son METAR: {self._metar_decoded(metar)}",
                f"└ Gözlem zamanı: {_observation_time_text(metar, self.tz)}",
                "",
                "🤖 Model tahminleri:",
                *self._rich_model_lines(model_bundle, analysis, previous_model_tmax_c),
                "",
                "👉 Meteorolojik Veriler",
                *self._meteorological_data_lines(metar, model_bundle, analysis),
                "",
                "Bulut dinamiği+:",
                *self._cloud_dynamics_lines(
                    analysis=analysis,
                    metar=metar,
                    taf=taf,
                    model_bundle=model_bundle,
                    recent_observations=recent_observations or [],
                ),
                "",
                "AI Etki Analizi:",
                *self._ai_effect_lines(analysis),
                "",
                "Neden bu tahmin?",
                *self._rationale_lines(analysis),
                "",
                f"{_weather_emoji(model_bundle, taf)} Bulut Aktiviteleri:",
                *self._cloud_activity_lines(model_bundle, taf),
                "",
                "🧑‍🏫 İşe yarar Forum Analizi:",
                "├ Forum/kullanıcı yorumu kaynağı bağlı değil; doğrulanmış yorum yok.",
                "└ Link eklenirse burada kaynaklı forum sinyali gösterilecek.",
                "",
                "🤖 AI Özeti:",
                *self._ai_summary_lines(analysis, market, model_bundle, metar),
                "",
                self._polymarket_price_line(analysis, market),
                "",
                f"⏳ Son Güncelleme: {report_time:%H:%M} ({self.settings.report_timezone})",
                "⚠️ Yeni değişkenler / son değişiklikler:",
                *self._change_lines(analysis, previous_analysis, temperature_momentum),
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


    def _market_headline(self, market: MarketSnapshot | None) -> str:
        if market is None:
            return "Polymarket marketi: ilgili market bulunamadı"
        title = escape(market.title or "Polymarket Ankara marketi")
        link = escape(market.link, quote=True)
        headline = f'<a href="{link}">{title}</a>' if market.link else title
        return f"{headline} · Vol: ${_fmt_num(market.volume)}"

    def _market_close_text(self, market: MarketSnapshot | None) -> str:
        if market is None:
            return "veri yok"
        close_time = _extract_market_close(market)
        if close_time is not None:
            return f"{close_time.astimezone(self.tz):%b %-d %-H:%M} Türkiye saati"
        if market.target_date is not None:
            return f"{market.target_date:%b %-d} kapanış saati veri yok"
        return "veri yok"

    def _hourly_expectation_lines(self, bundle: ModelBundle | None, temperature_momentum: tuple[float, int] | None) -> list[str]:
        hourly = _hourly_temperature_averages(bundle)
        lines: list[str] = []
        peak_hour = max(hourly, key=hourly.get) if hourly else None
        for hour in (9, 11, 13, 15):
            value = hourly.get(hour)
            suffix = " peak" if peak_hour == hour else ""
            arrow = "→ " if hour >= 13 else ""
            lines.append(f"├ {hour}:00 {arrow}{_fmt_c(value)}{suffix}")
        lines.append(f"├ 17:00 → {_cloud_trend_text(bundle, 17)}")
        lines.append(f"├ 19:00 → {_cooling_text(hourly, 19)}")
        lines.append("├ Sıcaklık Momentum")
        if temperature_momentum is None:
            lines.extend(["│  Son 90 dk: veri yok", "└ → Momentum ölçümü için geçmiş METAR yok"])
            return lines
        delta, minutes = temperature_momentum
        direction = "artış" if delta > 0.05 else "düşüş" if delta < -0.05 else "yatay"
        strength = "Güçlü momentum" if abs(delta) >= 1.5 else "Orta momentum" if abs(delta) >= 0.6 else "Zayıf/yatay momentum"
        arrow = "↗" if delta > 0.05 else "↘" if delta < -0.05 else "→"
        lines.extend([f"│  Son {minutes} dk:", f"│  {delta:+.1f}°C {direction}", f"└ {arrow} {strength}"])
        return lines

    def _metar_decoded(self, metar: METARNormalized | None) -> str:
        if metar is None:
            return "METAR verisi yok"
        wind = f"{metar.wind_direction_deg if metar.wind_direction_deg is not None else 'VRB'}°/{metar.wind_speed_kt:.0f} kt"
        gust = f", hamle {metar.wind_gust_kt:.0f} kt" if metar.wind_gust_kt is not None else ""
        pressure = f", basınç {metar.pressure_hpa:.0f} hPa" if metar.pressure_hpa is not None else ""
        return (
            f"{metar.station}: canlı hava {_fmt_c(metar.temperature_c)}, çiğ noktası {_fmt_c(metar.dew_point_c)}, "
            f"rüzgâr {wind}{gust}{pressure}. Isınma verimi çiğ noktası/rüzgâr ve bulutla birlikte değerlendiriliyor."
        )

    def _rich_model_lines(
        self,
        bundle: ModelBundle | None,
        analysis: ForecastAnalysis | None = None,
        previous_model_tmax_c: Mapping[str, float | None] | None = None,
    ) -> list[str]:
        if bundle is None:
            return ["└ Model verisi yok"]
        lines = []
        for forecast in bundle.forecasts:
            label = _model_link(forecast.model)
            previous_tmax = previous_model_tmax_c.get(forecast.model) if previous_model_tmax_c else None
            value = _fmt_c_with_trend(forecast.tmax_c, previous_tmax) if forecast.available else "unavailable"
            weight = ""
            if analysis and forecast.model in analysis.model_weights:
                weight = f" (ağırlık %{analysis.model_weights[forecast.model] * 100:.0f})"
            lines.append(f"├ {label}: {value}{weight}")
        values = [forecast.tmax_c for forecast in bundle.available_forecasts if forecast.tmax_c is not None]
        if values:
            lines.append(f"└ Model aralığı: {min(values):.1f}°C - {max(values):.1f}°C")
        else:
            lines.append("└ Model aralığı: veri yok")
        return lines

    def _meteorological_data_lines(
        self,
        metar: METARNormalized | None,
        bundle: ModelBundle | None,
        analysis: ForecastAnalysis,
    ) -> list[str]:
        precip_probability, precip_mm = _precipitation_summary(bundle)
        dew_point = metar.dew_point_c if metar else _hourly_average(bundle, "dew_point_2m_c")
        humidity = metar.relative_humidity if metar and metar.relative_humidity is not None else _hourly_average(bundle, "relative_humidity_pct")
        cape = _hourly_max(bundle, "cape_jkg")
        pressure = metar.pressure_hpa if metar and metar.pressure_hpa is not None else _hourly_average(bundle, "pressure_msl_hpa")
        wind = _wind_text(metar, bundle)
        synoptic = _adjustment(analysis, "synoptic_pressure")
        advection = _adjustment(analysis, "advection")
        return [
            f"├ Canlı sıcaklık: {_fmt_c(metar.temperature_c if metar else None)}",
            f"├ Yağış olasılığı: {_fmt_pct(precip_probability)}",
            f"├ Yağış milimetresi: {_fmt_num(precip_mm)} mm",
            f"├ Çiğ noktası: {_fmt_c(dew_point)}",
            f"├ Nem: {_fmt_humidity(humidity)}",
            "├ LI / TT: veri kaynağı bağlı değil; kararsızlık CAPE, basınç trendi ve TAF CB/TS sinyaliyle izleniyor.",
            f"├ Radyasyon sisi / inversiyon: {_inversion_text(metar, bundle)}",
            f"├ Topografik rüzgârlar: {_topographic_wind_text(metar, bundle)}",
            "├ CIN: veri kaynağı bağlı değil; CAPE yüksek ama tetikleyici zayıfsa konveksiyon bastırılmış kabul edilir.",
            f"├ CAPE: {_cape_text(cape)}",
            f"├ Rüzgâr: {wind}",
            f"├ Basınç: {_pressure_text(pressure, synoptic)}",
            f"└ Sıcaklık Adveksiyonu: {escape(advection.summary) if advection else 'veri yok'}",
        ]

    def _cloud_activity_lines(self, bundle: ModelBundle | None, taf: TAFNormalized | None) -> list[str]:
        low = _hourly_average(bundle, "cloud_cover_low_pct")
        mid = _hourly_average(bundle, "cloud_cover_mid_pct")
        high = _hourly_average(bundle, "cloud_cover_high_pct")
        total = _hourly_average(bundle, "cloud_cover_pct")
        precip_probability, _ = _precipitation_summary(bundle)
        rain_text = "Yağmur yüklü bulutlar ağırlıklı" if (precip_probability or 0.0) >= 0.35 or bool(taf and taf.rain_or_storm_risk) else "Yağış yüklü bulut sinyali zayıf"
        return [
            f"├ {rain_text}",
            f"├ Bulut yönü: {_cloud_direction_text(bundle)}",
            f"├ Bulut sayısı / kapalılık: {_fmt_pct_from_0_100(total)}",
            f"├ Tahmini açılma zamanı: {_clearing_time_text(bundle)}",
            f"└ Alçak/orta/yüksek bulutlar: alçak {_fmt_pct_from_0_100(low)}, orta {_fmt_pct_from_0_100(mid)}, yüksek {_fmt_pct_from_0_100(high)}",
        ]

    def _ai_summary_lines(
        self,
        analysis: ForecastAnalysis,
        market: MarketSnapshot | None,
        bundle: ModelBundle | None,
        metar: METARNormalized | None,
    ) -> list[str]:
        candidate = _best_market_candidate(analysis, market)
        model_values = [forecast.tmax_c for forecast in (bundle.available_forecasts if bundle else []) if forecast.tmax_c is not None]
        model_summary = (
            f"Modeller {min(model_values):.1f}-{max(model_values):.1f}°C bandında; bot merkezi {_fmt_c(analysis.final_tmax_c)}."
            if model_values
            else "Model tarafında yeterli veri yok; güven skoru aşağı çekiliyor."
        )
        metar_summary = (
            f"Canlı METAR {_fmt_c(metar.temperature_c)} ve çiğ noktası {_fmt_c(metar.dew_point_c)}; gün içi tavanı rüzgâr/bulut belirleyecek."
            if metar
            else "Canlı METAR yok; rapor model ağırlığıyla çalışıyor."
        )
        market_summary = (
            f"En güçlü fiyat/fair ayrışması {candidate[1]} için {_fmt_pp(candidate[0])}."
            if candidate and candidate[0] >= 0.05
            else "Piyasa tarafında net pozitif edge sinyali yok."
        )
        return [
            f"├ {escape(model_summary)}",
            f"├ {escape(metar_summary)}",
            f"├ {escape(market_summary)}",
            f"├ Net karar: {_fmt_integer_c(analysis.final_tmax_c)} merkezi izlenir.",
            "└ Not: Yatırım tavsiyesi değildir.",
        ]

    def _polymarket_price_line(self, analysis: ForecastAnalysis, market: MarketSnapshot | None) -> str:
        candidate = _best_market_candidate(analysis, market)
        if candidate is None:
            return "💵 Polymarket Canlı Fiyat: veri yok"
        _, bracket, _, implied = candidate
        movement = _recent_trade_movement(market, bracket) if market else None
        if movement is None:
            return f"💵 Polymarket Canlı Fiyat ({escape(bracket)}): {_fmt_cents(implied)}"
        icon = "🟢" if movement > 0 else "🔴" if movement < 0 else "🟡"
        return f"💵 Polymarket Canlı Fiyat ({escape(bracket)}): {_fmt_cents(implied)} {icon} (son işlemlerde {movement * 100:+.1f} pp)"

    def _change_lines(
        self,
        analysis: ForecastAnalysis,
        previous_analysis: ForecastAnalysis | None,
        temperature_momentum: tuple[float, int] | None,
    ) -> list[str]:
        lines: list[str] = []
        if previous_analysis and analysis.final_tmax_c is not None and previous_analysis.final_tmax_c is not None:
            delta = analysis.final_tmax_c - previous_analysis.final_tmax_c
            lines.append(f"├ Tahmin değişimi: {_trend_text(delta)}")
        else:
            lines.append("├ Tahmin değişimi: önceki rapor yok")
        if temperature_momentum is not None:
            lines.append(f"├ Canlı momentum: {_trend_text(temperature_momentum[0])}")
        if analysis.model_spread_c is not None:
            lines.append(f"└ Model ayrışması: {analysis.model_spread_c:.1f}°C")
        else:
            lines.append("└ Model ayrışması: veri yok")
        return lines

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
        lines = [
            _bullet(f"Canlı sapma: {_adj(lookup.get('live_observation'))}"),
            _bullet(f"Rüzgâr/adveksiyon: {_adj(lookup.get('advection'))}"),
            _bullet(f"Basınç/üst seviye: {_adj(lookup.get('synoptic_pressure'))}"),
            _bullet(f"Bulut/radyasyon: {_adj(lookup.get('cloud_radiation'))}"),
            _bullet(f"Yağış/zemin: {_adj(lookup.get('rain_soil'))}"),
        ]
        microclimate = lookup.get("ltac_microclimate")
        if microclimate is not None and microclimate.value_c != 0:
            lines.append(_bullet(f"İstasyon değişkeni: {_adj(microclimate)}"))
        return lines

    def _cloud_dynamics_lines(
        self,
        *,
        analysis: ForecastAnalysis,
        metar: METARNormalized | None,
        taf: TAFNormalized | None,
        model_bundle: ModelBundle | None,
        recent_observations: list[dict[str, Any]],
    ) -> list[str]:
        cloud_pct = _cloud_density_pct(analysis, metar, model_bundle)
        sunshine_pct = _sunshine_pct(analysis, model_bundle)
        rain_status = _rain_cell_status(taf, model_bundle)
        opening_status = _opening_after_15(model_bundle)
        wind_direction = _wind_direction_for_map(metar, model_bundle)
        trend = _recent_temperature_trend(recent_observations, metar)
        runway_series = _runway_temperature_series(model_bundle, _adjustment_value(analysis, "ltac_microclimate"))
        return [
            _bullet(f"Canlı uydu GIF/link: {self.settings.satellite_motion_url}"),
            _bullet(f"Radar motion: {self.settings.radar_motion_url}"),
            _bullet(f"Bulut yoğunluğu: {_fmt_whole_pct(cloud_pct)}"),
            _bullet(f"Güneşlenme: {_fmt_whole_pct(sunshine_pct)}"),
            _bullet(f"Yağış hücresi: {rain_status}"),
            _bullet(f"15:00 sonrası: {opening_status}"),
            _bullet("Radar motion ASCII mini map:"),
            *_ascii_radar_map(wind_direction, model_bundle),
            *_sky_heatmap_lines(model_bundle),
            _bullet(f"Pist sıcaklık grafiği: {runway_series}"),
            _bullet(f"Son 6 saat trend: {trend}"),
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


def _fmt_integer_c(value: float | None) -> str:
    return f"{round(value):.0f}°C" if value is not None else "veri yok"


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


def _fmt_pct_from_0_100(value: float | None) -> str:
    return f"%{value:.0f}" if value is not None else "veri yok"


def _fmt_humidity(value: float | int | None) -> str:
    return f"%{value:.0f}" if value is not None else "veri yok"


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



def _cloud_density_pct(
    analysis: ForecastAnalysis,
    metar: METARNormalized | None,
    model_bundle: ModelBundle | None,
) -> float | None:
    model_values = [value for _, value in _hourly_cloud_series(model_bundle, range(10, 15))]
    if model_values:
        return round(mean(model_values), 1)
    layer_values = [
        _adjustment_input(analysis, "cloud_radiation", "low_cloud_mean_pct"),
        _adjustment_input(analysis, "cloud_radiation", "mid_cloud_mean_pct"),
        _adjustment_input(analysis, "cloud_radiation", "high_cloud_mean_pct"),
    ]
    layer_values = [value for value in layer_values if value is not None]
    if layer_values:
        return round(max(layer_values), 1)
    if metar and metar.cloud_layers:
        return _metar_cloud_density(metar.cloud_layers)
    return None


def _sunshine_pct(analysis: ForecastAnalysis, model_bundle: ModelBundle | None) -> float | None:
    candidates: list[float] = []
    shortwave = [value for _, value in _hourly_mean_series(model_bundle, "shortwave_radiation_wm2", range(10, 15))]
    if shortwave:
        candidates.append(max(shortwave) / 850.0 * 100.0)
    low = _adjustment_input(analysis, "cloud_radiation", "low_cloud_mean_pct")
    mid = _adjustment_input(analysis, "cloud_radiation", "mid_cloud_mean_pct")
    high = _adjustment_input(analysis, "cloud_radiation", "high_cloud_mean_pct")
    opacity_parts = [
        (low, 0.70),
        (mid, 0.45),
        (high, 0.25),
    ]
    opacity = sum(value * weight for value, weight in opacity_parts if value is not None)
    if opacity:
        candidates.append(100.0 - min(100.0, opacity))
    elif (cloud_pct := _cloud_density_pct(analysis, None, model_bundle)) is not None:
        candidates.append(100.0 - cloud_pct * 0.65)
    if not candidates:
        return None
    return round(max(0.0, min(100.0, mean(candidates))), 1)


def _rain_cell_status(taf: TAFNormalized | None, model_bundle: ModelBundle | None) -> str:
    precip = _hourly_mean_series(model_bundle, "precipitation_mm", range(9, 19))
    taf_risk = bool(taf and taf.rain_or_storm_risk)
    if not precip:
        return "TAF yağış/CB riski var; canlı radar takip" if taf_risk else "veri yok"
    early = [value for hour, value in precip if 9 <= hour <= 14]
    late = [value for hour, value in precip if hour >= 15]
    early_mean = mean(early) if early else 0.0
    late_mean = mean(late) if late else 0.0
    max_precip = max(value for _, value in precip)
    if max_precip < 0.1 and not taf_risk:
        return "zayıf / belirgin hücre yok"
    if late_mean + 0.05 < early_mean:
        return f"zayıflıyor ({early_mean:.2f}→{late_mean:.2f} mm/saat)"
    if late_mean > early_mean + 0.05:
        return f"güçleniyor ({early_mean:.2f}→{late_mean:.2f} mm/saat)"
    if taf_risk:
        return "TAF yağış/CB riski var; radar teyidi gerekli"
    return f"zayıf-stabil (maks {max_precip:.2f} mm/saat)"


def _opening_after_15(model_bundle: ModelBundle | None) -> str:
    cloud = _hourly_cloud_series(model_bundle, range(11, 19))
    if not cloud:
        return "veri yok"
    early = [value for hour, value in cloud if 11 <= hour <= 14]
    late = [value for hour, value in cloud if hour >= 15]
    if not early or not late:
        return "veri yok"
    early_mean = mean(early)
    late_mean = mean(late)
    if late_mean <= early_mean - 12.0:
        return f"gökyüzü açabilir (bulut %{early_mean:.0f}→%{late_mean:.0f})"
    if late_mean <= 45.0:
        return f"parçalı/açık kalabilir (15+ bulut %{late_mean:.0f})"
    return f"net açılma sinyali yok (15+ bulut %{late_mean:.0f})"


def _ascii_radar_map(wind_direction: float | None, model_bundle: ModelBundle | None) -> list[str]:
    flow = _wind_flow_label(wind_direction)
    rain_marker = _rain_marker(model_bundle)
    return [
        "  NW       N       NE",
        f"  W     LTAC     E    akış: {flow}",
        f"  SW       S       SE   hücre: {rain_marker}",
    ]


def _sky_heatmap_lines(model_bundle: ModelBundle | None) -> list[str]:
    cloud = _hourly_cloud_series(model_bundle, range(10, 19))
    if not cloud:
        return [_bullet("Heatmap 10-18: veri yok")]
    hours = [hour for hour, _ in cloud]
    cloud_values = [value for _, value in cloud]
    sunshine_values = [max(0.0, min(100.0, 100.0 - value * 0.65)) for value in cloud_values]
    precip_lookup = dict(_hourly_mean_series(model_bundle, "precipitation_mm", hours))
    precip_values = [precip_lookup.get(hour, 0.0) for hour in hours]
    precip_max = max(1.0, max(precip_values, default=0.0))
    return [
        _bullet("Heatmap 10-18:"),
        f"  Saat  : {' '.join(f'{hour:02d}' for hour in hours)}",
        f"  Bulut : {_sparkline(cloud_values, 0.0, 100.0)}",
        f"  Güneş : {_sparkline(sunshine_values, 0.0, 100.0)}",
        f"  Yağış : {_sparkline(precip_values, 0.0, precip_max)}",
    ]


def _runway_temperature_series(model_bundle: ModelBundle | None, offset_c: float) -> str:
    temps = _hourly_mean_series(model_bundle, "temperature_2m_c", range(10, 19))
    if not temps:
        return "veri yok"
    values = [value + offset_c for _, value in temps]
    hours = [hour for hour, _ in temps]
    return f"{hours[0]:02d}-{hours[-1]:02d} {values[0]:.1f}→{values[-1]:.1f}°C {_sparkline(values)} (offset {offset_c:+.1f}°C)"


def _recent_temperature_trend(rows: list[dict[str, Any]], metar: METARNormalized | None) -> str:
    values = [_safe_float(row.get("tmpc")) for row in rows]
    values = [value for value in values if value is not None]
    if metar and (not values or abs(values[-1] - metar.temperature_c) > 0.05):
        values.append(metar.temperature_c)
    if not values:
        return "veri yok"
    if len(values) == 1:
        return f"{values[0]:.1f}°C (tek gözlem)"
    delta = values[-1] - values[0]
    return f"{values[0]:.1f}→{values[-1]:.1f}°C ({delta:+.1f}) {_sparkline(values)}"


def _hourly_cloud_series(model_bundle: ModelBundle | None, hours: range) -> list[tuple[int, float]]:
    return _hourly_point_series(model_bundle, hours, _cloud_cover_for_point)


def _hourly_mean_series(model_bundle: ModelBundle | None, attr: str, hours: range | list[int]) -> list[tuple[int, float]]:
    return _hourly_point_series(model_bundle, hours, lambda point: getattr(point, attr, None))


def _hourly_point_series(model_bundle: ModelBundle | None, hours: range | list[int], getter: Any) -> list[tuple[int, float]]:
    if model_bundle is None:
        return []
    series: list[tuple[int, float]] = []
    for hour in hours:
        values = []
        for forecast in model_bundle.forecasts:
            for point in forecast.hourly:
                if point.time.hour != hour:
                    continue
                value = getter(point)
                if value is not None:
                    values.append(float(value))
        if values:
            series.append((int(hour), mean(values)))
    return series


def _cloud_cover_for_point(point: Any) -> float | None:
    if point.cloud_cover_pct is not None:
        return float(point.cloud_cover_pct)
    layers = [
        point.cloud_cover_low_pct,
        point.cloud_cover_mid_pct,
        point.cloud_cover_high_pct,
    ]
    values = [float(value) for value in layers if value is not None]
    return max(values) if values else None


def _metar_cloud_density(clouds: list[dict]) -> float | None:
    cover_map = {
        "SKC": 0.0,
        "CLR": 0.0,
        "NSC": 0.0,
        "NCD": 0.0,
        "FEW": 20.0,
        "SCT": 45.0,
        "BKN": 75.0,
        "OVC": 95.0,
    }
    values = []
    for cloud in clouds:
        cover = str(cloud.get("cover") or cloud.get("type") or "").upper()
        if cover in cover_map:
            values.append(cover_map[cover])
    return max(values) if values else None


def _wind_direction_for_map(metar: METARNormalized | None, model_bundle: ModelBundle | None) -> float | None:
    if metar and metar.wind_direction_deg is not None:
        return float(metar.wind_direction_deg)
    values = [value for _, value in _hourly_mean_series(model_bundle, "wind_direction_10m_deg", range(10, 15))]
    return _circular_mean(values) if values else None


def _rain_marker(model_bundle: ModelBundle | None) -> str:
    values = [value for _, value in _hourly_mean_series(model_bundle, "precipitation_mm", range(9, 19))]
    if not values or max(values) < 0.1:
        return "· zayıf"
    if max(values) < 0.5:
        return "~ orta/zayıf"
    return "█ aktif"


def _wind_flow_label(direction: float | None) -> str:
    if direction is None:
        return "veri yok"
    source = _cardinal(direction)
    target = _cardinal((direction + 180.0) % 360.0)
    return f"{source}→{target}"


def _cardinal(direction: float) -> str:
    labels = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return labels[int((direction + 22.5) // 45) % 8]


def _sparkline(values: list[float], minimum: float | None = None, maximum: float | None = None) -> str:
    if not values:
        return "veri yok"
    blocks = "▁▂▃▄▅▆▇█"
    low = min(values) if minimum is None else minimum
    high = max(values) if maximum is None else maximum
    if high <= low:
        return blocks[0] * len(values)
    chars = []
    for value in values:
        ratio = max(0.0, min(1.0, (value - low) / (high - low)))
        chars.append(blocks[round(ratio * (len(blocks) - 1))])
    return "".join(chars)


def _adjustment_value(analysis: ForecastAnalysis, name: str) -> float:
    adjustment = next((item for item in analysis.adjustments if item.name == name), None)
    return adjustment.value_c if adjustment is not None else 0.0


def _adjustment_input(analysis: ForecastAnalysis, name: str, key: str) -> float | None:
    adjustment = next((item for item in analysis.adjustments if item.name == name), None)
    if adjustment is None:
        return None
    return _safe_float(adjustment.inputs.get(key))


def _fmt_whole_pct(value: float | None) -> str:
    return f"%{value:.0f}" if value is not None else "veri yok"


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "M"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _circular_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    sin_sum = sum(math.sin(math.radians(value)) for value in values)
    cos_sum = sum(math.cos(math.radians(value)) for value in values)
    angle = math.degrees(math.atan2(sin_sum, cos_sum))
    return angle + 360 if angle < 0 else angle

def _weather_emoji(bundle: ModelBundle | None, taf: TAFNormalized | None) -> str:
    precip_probability, precip_mm = _precipitation_summary(bundle)
    cloud = _hourly_average(bundle, "cloud_cover_pct")
    cape = _hourly_max(bundle, "cape_jkg")
    if (cape or 0.0) >= 700.0 or bool(taf and taf.rain_or_storm_risk):
        return "🌩️"
    if (precip_mm or 0.0) >= 1.0:
        return "🌧️"
    if (precip_probability or 0.0) >= 0.25:
        return "🌦️"
    if cloud is None:
        return "☁️"
    if cloud >= 80.0:
        return "☁️"
    if cloud >= 55.0:
        return "🌥️"
    if cloud >= 30.0:
        return "⛅️"
    return "🌤️"


def _risk_emoji(boundary: str) -> str:
    return {"YÜKSEK": "🔴", "ORTA": "🟡", "DÜŞÜK": "🟢"}.get(boundary, "⚪️")


def _extract_market_close(market: MarketSnapshot) -> datetime | None:
    for key in ("endDate", "endDateIso", "closedTime", "closeTime", "resolutionDate"):
        value = market.raw_json.get(key)
        if not value:
            continue
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def _hourly_temperature_averages(bundle: ModelBundle | None) -> dict[int, float]:
    hourly: dict[int, list[float]] = {}
    if bundle is None:
        return {}
    for forecast in bundle.available_forecasts:
        for point in forecast.hourly:
            if point.temperature_2m_c is not None:
                hourly.setdefault(point.time.hour, []).append(point.temperature_2m_c)
    return {hour: sum(values) / len(values) for hour, values in hourly.items() if values}


def _hourly_average(bundle: ModelBundle | None, field: str, start_hour: int = 10, end_hour: int = 15) -> float | None:
    values = []
    if bundle is None:
        return None
    for forecast in bundle.available_forecasts:
        for point in forecast.hourly:
            if start_hour <= point.time.hour <= end_hour:
                value = getattr(point, field)
                if value is not None:
                    values.append(float(value))
    return sum(values) / len(values) if values else None


def _hourly_max(bundle: ModelBundle | None, field: str, start_hour: int = 10, end_hour: int = 15) -> float | None:
    values = []
    if bundle is None:
        return None
    for forecast in bundle.available_forecasts:
        for point in forecast.hourly:
            if start_hour <= point.time.hour <= end_hour:
                value = getattr(point, field)
                if value is not None:
                    values.append(float(value))
    return max(values) if values else None


def _precipitation_summary(bundle: ModelBundle | None) -> tuple[float | None, float | None]:
    totals = []
    if bundle is None:
        return None, None
    for forecast in bundle.available_forecasts:
        total = sum(point.precipitation_mm or 0.0 for point in forecast.hourly)
        totals.append(total)
    if not totals:
        return None, None
    probability = sum(1 for total in totals if total >= 0.2) / len(totals)
    return probability, sum(totals) / len(totals)


def _cloud_trend_text(bundle: ModelBundle | None, hour: int) -> str:
    cloud = _hourly_average(bundle, "cloud_cover_pct", hour, hour)
    if cloud is None:
        return "bulut verisi yok"
    if cloud >= 70.0:
        return "Bulut artışı"
    if cloud <= 30.0:
        return "Hava açıyor"
    return "Parçalı bulut"


def _cooling_text(hourly: dict[int, float], hour: int) -> str:
    value = hourly.get(hour)
    afternoon = hourly.get(15) or hourly.get(14)
    if value is None:
        return "Soğuma verisi yok"
    if afternoon is not None and value < afternoon - 0.4:
        return "Soğuma başlangıcı"
    return f"{_fmt_c(value)} civarı"


def _observation_time_text(metar: METARNormalized | None, tz: ZoneInfo) -> str:
    if metar is None:
        return "veri yok"
    return f"{metar.observation_time.astimezone(tz):%H:%M}"


def _model_link(model: str) -> str:
    label = _display_model_name(model)
    lowered = model.lower()
    if "ecmwf" in lowered:
        url = "https://www.ecmwf.int/en/forecasts"
    elif "gfs" in lowered:
        url = "https://www.ncei.noaa.gov/products/weather-climate-models/global-forecast"
    elif "icon" in lowered:
        url = "https://www.dwd.de/EN/ourservices/nwp_forecast_data/nwp_forecast_data.html"
    elif "visual" in lowered:
        url = "https://www.visualcrossing.com/weather-api/"
    elif "tomorrow" in lowered:
        url = "https://www.tomorrow.io/weather-api/"
    else:
        url = "https://open-meteo.com/en/docs"
    return f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'


def _wind_text(metar: METARNormalized | None, bundle: ModelBundle | None) -> str:
    if metar is not None:
        direction = metar.wind_direction_deg if metar.wind_direction_deg is not None else "VRB"
        base = f"{direction}° / {metar.wind_speed_kt:.0f} KT"
        if metar.wind_speed_kt >= 15.0:
            return f"{base}; kuvvetli karışım, ısınmayı yüzeye taşır ama rüzgâr soğutması artar."
        if metar.wind_speed_kt <= 4.0:
            return f"{base}; zayıf rüzgâr, inversiyon/sis riski ve yavaş karışım."
        return f"{base}; orta seviye karışım, sıcaklık tavanı için nötr/sağlıklı."
    speed = _hourly_average(bundle, "wind_speed_10m_kt")
    direction = _hourly_average(bundle, "wind_direction_10m_deg")
    if speed is None:
        return "veri yok"
    return f"{direction:.0f}° / {speed:.0f} KT model ort.; canlı METAR yok."


def _inversion_text(metar: METARNormalized | None, bundle: ModelBundle | None) -> str:
    temp_850 = _hourly_average(bundle, "temperature_850hpa_c", 6, 10)
    surface = metar.temperature_c if metar is not None else _hourly_average(bundle, "temperature_2m_c", 6, 10)
    if temp_850 is None or surface is None:
        return "850 hPa/yüzey farkı veri yok"
    diff = surface - temp_850
    if diff < 1.5:
        return f"yüzey-850 hPa farkı {diff:.1f}°C; inversiyon/radyasyon sisi riski yüksek."
    if diff < 4.0:
        return f"yüzey-850 hPa farkı {diff:.1f}°C; zayıf inversiyon ihtimali var."
    return f"yüzey-850 hPa farkı {diff:.1f}°C; karışım daha iyi, sis riski sınırlı."


def _topographic_wind_text(metar: METARNormalized | None, bundle: ModelBundle | None) -> str:
    speed = metar.wind_speed_kt if metar is not None else _hourly_average(bundle, "wind_speed_10m_kt")
    direction = metar.wind_direction_deg if metar is not None else _hourly_average(bundle, "wind_direction_10m_deg")
    if speed is None:
        return "rüzgâr verisi yok"
    if speed <= 5.0:
        return "zayıf rüzgâr; gece/vadi drenajı ve pist çevresi ani yön değişimi daha olası."
    if direction is not None and (float(direction) >= 300 or float(direction) <= 60):
        return "kuzey sektörlü akış; Çubuk Ovası boyunca serin/kurutucu kanal etkisi izlenir."
    return "belirgin katabatik/anabatik alarm yok; yön ve hamleler METAR ile izleniyor."


def _cape_text(value: float | None) -> str:
    if value is None:
        return "veri yok; konvektif enerji hesaplanamadı."
    if value >= 1000.0:
        return f"{value:.0f} J/kg; CB/oraj ve ani yağış riski yüksek."
    if value >= 400.0:
        return f"{value:.0f} J/kg; tetikleyici gelirse konveksiyon mümkün."
    return f"{value:.0f} J/kg; dikey enerji zayıf, fırtına riski sınırlı."


def _pressure_text(value: float | None, synoptic: object | None) -> str:
    if value is None:
        return "veri yok"
    trend = None
    if synoptic is not None:
        trend = synoptic.inputs.get("pressure_trend_hpa")
    trend_text = ""
    if trend is not None:
        trend = float(trend)
        if trend <= -1.5:
            trend_text = "; düşüş konveksiyon/bulut riskini artırır"
        elif trend >= 1.5:
            trend_text = "; yükseliş daha stabil/açık hava lehine"
        else:
            trend_text = "; trend nötr"
    return f"{value:.0f} hPa{trend_text}"


def _cloud_direction_text(bundle: ModelBundle | None) -> str:
    direction = _hourly_average(bundle, "wind_direction_850hpa_deg")
    speed = _hourly_average(bundle, "wind_speed_850hpa_kt")
    if direction is None:
        return "veri yok"
    suffix = f", {speed:.0f} kt üst seviye akış" if speed is not None else ""
    return f"{direction:.0f}° yönlü taşıma{suffix}"


def _clearing_time_text(bundle: ModelBundle | None) -> str:
    if bundle is None:
        return "veri yok"
    hourly: dict[int, list[float]] = {}
    for forecast in bundle.available_forecasts:
        for point in forecast.hourly:
            if point.cloud_cover_pct is not None:
                hourly.setdefault(point.time.hour, []).append(point.cloud_cover_pct)
    for hour in range(12, 21):
        values = hourly.get(hour)
        if values and sum(values) / len(values) <= 35.0:
            return f"{hour}:00 civarı açılma sinyali"
    return "gün içinde net açılma sinyali yok"


def _adjustment(analysis: ForecastAnalysis, name: str) -> object | None:
    return next((item for item in analysis.adjustments if item.name == name), None)


def _recent_trade_movement(market: MarketSnapshot | None, bracket: str) -> float | None:
    if market is None:
        return None
    outcome = next((item for item in market.outcomes if item.bracket == bracket), None)
    if outcome is None or len(outcome.recent_trades) < 2:
        return None
    prices = []
    for trade in outcome.recent_trades:
        for key in ("price", "yes_price", "outcomePrice"):
            value = trade.get(key)
            if value is None:
                continue
            try:
                prices.append(float(value))
                break
            except (TypeError, ValueError):
                continue
    if len(prices) < 2:
        return None
    return prices[0] - prices[-1]


def _trend_text(delta: float) -> str:
    if delta > 0.05:
        return f"🔺 {delta:+.1f}°C"
    if delta < -0.05:
        return f"🔻 {delta:+.1f}°C"
    return "→ yatay"


def _adj(adjustment: object | None) -> str:
    if adjustment is None:
        return "veri yok"
    return f"{adjustment.summary} ({adjustment.value_c:+.1f}°C)"
