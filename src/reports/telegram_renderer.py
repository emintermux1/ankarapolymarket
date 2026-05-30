from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.schemas import (
    ActualResult,
    AviationSourceSnapshot,
    ForecastAnalysis,
    ForumAnalysis,
    MarketSnapshot,
    METARNormalized,
    ModelBundle,
    NearbySensorSnapshot,
    SourceHealth,
    TAFNormalized,
)
from src.forecast.upper_air import calculate_upper_air_profile_adjustment


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
        recent_observations: list[dict[str, Any]] | None = None,
        nearby_sensors: list[NearbySensorSnapshot] | None = None,
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
                "Yakın canlı sensörler:",
                *self._nearby_sensor_lines(nearby_sensors or [], metar=metar, limit=4),
                "",
                "🚨 YUVARLAMA ALARMI:",
                *self._rounding_alarm_lines(analysis, metar, taf),
                "",
                "Model tahminleri:",
                *self._model_lines(model_bundle, analysis, previous_model_tmax_c),
                "",
                "Hava dinamiği:",
                *self._dynamic_lines(analysis),
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

    def hourly_max_forecast(
        self,
        *,
        analysis: ForecastAnalysis,
        metar: METARNormalized | None,
        model_bundle: ModelBundle | None,
        market: MarketSnapshot | None,
        recent_observations: list[dict[str, Any]] | None = None,
        nearby_sensors: list[NearbySensorSnapshot] | None = None,
        previous_analysis: ForecastAnalysis | None = None,
    ) -> str:
        report_time = analysis.generated_at.astimezone(self.tz)
        settlement = _nearest_settlement_integer(analysis.final_tmax_c)
        observed_peak = _observed_peak_c(recent_observations or [], metar)
        trend = _recent_temperature_trend(recent_observations or [], metar)
        return "\n".join(
            [
                f"🎯 {report_time:%H:00} SAAT BAŞI MAX TAHMİNİ · LTAC",
                "",
                f"Bugünün göreceği max: {_fmt_integer_c(settlement)}",
                f"Model merkezi: {_fmt_c_with_trend(analysis.final_tmax_c, previous_analysis.final_tmax_c if previous_analysis else None)}",
                f"Ana aralık: {_fmt_range(analysis.main_range_low_c, analysis.main_range_high_c)}",
                "",
                f"Canlı sıcaklık: {_fmt_c(metar.temperature_c if metar else None)}",
                f"Gün içi ölçülen max: {_fmt_c(observed_peak)}",
                _remaining_warming_line(analysis, observed_peak),
                f"Son trend: {trend}",
                *_compact_nearby_sensor_lines(nearby_sensors or [], metar),
                "",
                f"Güven: {analysis.confidence_score}/100 ({_confidence_label(analysis.confidence_score)})",
                f"Sınır riski: {_boundary_risk(analysis)}",
                _rounding_distance_line(analysis.final_tmax_c),
                "",
                _hourly_market_line(analysis, market),
                _hourly_decision_line(analysis, market, self.settings),
                "",
                f"Sonraki kontrol: {_next_hour_label(report_time)}",
                "Not: Yatırım tavsiyesi değildir; final WU/NOAA kaydıyla teyit edilir.",
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
        return "\n".join([f"{metar.station} SON GÖZLEM", *self._metar_lines(metar)])

    def metar_alert(self, metar: METARNormalized, nearby_sensors: list[NearbySensorSnapshot] | None = None) -> str:
        observed_local = metar.observation_time.astimezone(self.tz)
        fetched_local = metar.fetch_timestamp.astimezone(self.tz)
        return "\n".join(
            [
                f"🚨 {metar.station} YENİ METAR/SENSÖR",
                f"Zaman: {observed_local:%Y-%m-%d %H:%M} {self.tz.key} · {metar.observation_time:%H:%M} UTC",
                f"Kaynak: {metar.source} · çekim {fetched_local:%H:%M:%S}",
                "Kaynak linkleri:",
                *_metar_source_lines(metar),
                "",
                f"Sıcaklık: {metar.temperature_c:.1f}°C · çiy {metar.dew_point_c:.1f}°C · nem %{metar.relative_humidity if metar.relative_humidity is not None else 'veri yok'}",
                f"Rüzgâr: {_metar_wind_text(metar)}",
                f"Basınç: {_fmt_num(metar.pressure_hpa)} hPa",
                f"Görüş: {_format_visibility_m(metar.visibility_m)}",
                f"Bulut: {_format_clouds(metar.cloud_layers)}",
                f"Hava olayı: {_metar_weather_text(metar)}",
                "",
                *_metar_extra_sensor_lines(metar),
                "",
                "Yakın canlı sensörler:",
                *self._nearby_sensor_lines(nearby_sensors or [], metar=metar, limit=5),
                "",
                f"Raw: {metar.raw_text or 'veri yok'}",
            ]
        )

    def aviation_source_digest(self, snapshots: list[AviationSourceSnapshot], new_snapshot_keys: set[str] | None = None) -> str:
        if not snapshots:
            return "Havacılık kaynak özeti: yeni veri yok."
        new_snapshot_keys = new_snapshot_keys or {_aviation_source_snapshot_key(snapshot) for snapshot in snapshots}
        fetched_local = max(snapshot.fetch_timestamp for snapshot in snapshots).astimezone(self.tz)
        new_count = sum(1 for snapshot in snapshots if _aviation_source_snapshot_key(snapshot) in new_snapshot_keys)
        lines = [
            "🛰 HAVACILIK KAYNAK ÖZETİ",
            f"Çekim: {fetched_local:%Y-%m-%d %H:%M:%S} {self.tz.key}",
            f"Kaynak: {len(snapshots)} kayıt · yeni: {new_count}",
            "",
        ]
        for station in sorted({snapshot.station for snapshot in snapshots}):
            lines.append(station)
            for snapshot in sorted(
                [item for item in snapshots if item.station == station],
                key=lambda item: (item.source, item.kind),
            ):
                marker = "yeni" if _aviation_source_snapshot_key(snapshot) in new_snapshot_keys else "aynı"
                observed = snapshot.observed_at.astimezone(self.tz) if snapshot.observed_at else None
                observed_text = f" · veri {observed:%H:%M}" if observed else ""
                summary = _compact_source_summary(snapshot)
                lines.append(f"• [{marker}] {snapshot.source}: {summary}{observed_text}")
                lines.append(f"  Link: {snapshot.source_url}")
            lines.append("")
        return "\n".join(lines).strip()

    def aviation_source_alert(self, snapshot: AviationSourceSnapshot) -> str:
        fetched_local = snapshot.fetch_timestamp.astimezone(self.tz)
        observed = snapshot.observed_at.astimezone(self.tz) if snapshot.observed_at else None
        lines = [
            f"🛰 {snapshot.station} KAYNAK GÜNCELLEMESİ",
            f"Kaynak: {snapshot.source} · {snapshot.kind}",
            f"Çekim: {fetched_local:%Y-%m-%d %H:%M:%S} {self.tz.key}",
        ]
        if observed:
            lines.append(f"Veri zamanı: {observed:%Y-%m-%d %H:%M} {self.tz.key} · {snapshot.observed_at:%H:%M} UTC")
        lines.extend(
            [
                f"Link: {snapshot.source_url}",
                "",
                snapshot.title,
                *(_bullet(line) for line in snapshot.summary_lines[:8]),
            ]
        )
        return "\n".join(lines)

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

    def advanced_signals_report(self, bundle: ModelBundle | None) -> str:
        if bundle is None:
            return "\n".join(["İLERİ METEOROLOJİ SİNYALLERİ", _bullet("Model verisi yok")])
        return "\n".join(["İLERİ METEOROLOJİ SİNYALLERİ", *self._advanced_signal_lines(bundle)])

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

    def daily_alert(self, analysis: ForecastAnalysis, market: MarketSnapshot | None) -> str:
        rounded = (
            _settlement_integer_from_reported_temp(analysis.final_tmax_c)
            if analysis.final_tmax_c is not None
            else None
        )
        lines = [
            f"ANKARA TAHMİN · {analysis.target_date.isoformat()}",
            f"Tmax: {_fmt_c(analysis.final_tmax_c)}" + (f" → {rounded}°C" if rounded is not None else ""),
            (
                f"Aralık: {_fmt_range(analysis.main_range_low_c, analysis.main_range_high_c)} "
                f"· Güven: %{analysis.confidence_score}"
            ),
        ]
        candidate = _best_market_candidate(analysis, market)
        if candidate:
            edge, bracket, fair, implied = candidate
            lines.append(
                f"En iyi aday: {bracket} · fair {_fmt_pct(fair)} "
                f"· piyasa {_fmt_pct(implied)} · edge {_fmt_pp(edge)}"
            )
        if market and market.link:
            lines.append(f"Market: {market.link}")
        return "\n".join(lines)

    def forecast_change_alert(self, analysis: ForecastAnalysis, previous: Mapping[str, Any]) -> str:
        previous_tmax = _safe_float(previous.get("final_tmax_c"))
        previous_rounded = previous.get("rounded_tmax_c")
        current_rounded = (
            _settlement_integer_from_reported_temp(analysis.final_tmax_c)
            if analysis.final_tmax_c is not None
            else None
        )
        delta = (
            analysis.final_tmax_c - previous_tmax
            if analysis.final_tmax_c is not None and previous_tmax is not None
            else None
        )
        delta_text = f" ({delta:+.1f}°C)" if delta is not None else ""
        return "\n".join(
            [
                f"TAHMİN DEĞİŞTİ · {analysis.target_date.isoformat()}",
                f"Eski: {_fmt_c(previous_tmax)}"
                + (f" → {previous_rounded}°C" if previous_rounded is not None else ""),
                f"Yeni: {_fmt_c(analysis.final_tmax_c)}"
                + (f" → {current_rounded}°C" if current_rounded is not None else "")
                + delta_text,
                (
                    f"Güven: %{analysis.confidence_score} "
                    f"· Aralık: {_fmt_range(analysis.main_range_low_c, analysis.main_range_high_c)}"
                ),
            ]
        )

    def market_resolve_alert(self, result: ActualResult) -> str:
        return "\n".join(
            [
                f"ANKARA MARKET RESOLVE · {result.target_date.isoformat()}",
                f"Final: {_fmt_c(result.tmax_c)} → {result.rounded_tmax_c}°C",
                f"Kaynak: {result.source}",
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

    def _nearby_sensor_lines(
        self,
        sensors: list[NearbySensorSnapshot],
        *,
        metar: METARNormalized | None = None,
        limit: int = 5,
    ) -> list[str]:
        if not sensors:
            return [_bullet("Yakın sensör: veri yok")]
        lines: list[str] = []
        for sensor in sensors[:limit]:
            observed = sensor.observation_time.astimezone(self.tz) if sensor.observation_time else None
            observed_text = f"{observed:%H:%M}" if observed else "zaman yok"
            delta = (
                f" · METAR farkı {_fmt_signed_c(sensor.temperature_c - metar.temperature_c)}"
                if metar is not None and sensor.temperature_c is not None
                else ""
            )
            lines.append(
                _bullet(
                    f"{sensor.name}: {_fmt_c(sensor.temperature_c)}"
                    f"{delta} · hissedilen {_fmt_c(sensor.apparent_temperature_c)}"
                    f" · nem {_fmt_pct_from_whole(sensor.relative_humidity)}"
                    f" · rüzgâr {_format_wind(sensor.wind_direction_deg, sensor.wind_speed_kt)}"
                    f" · yağış {_fmt_mm(sensor.precipitation_mm)}"
                    f" · {observed_text} · {sensor.source}"
                )
            )
            lines.append(_bullet(f"Kaynak: {sensor.source_url}"))
        if len(sensors) > limit:
            lines.append(_bullet(f"Ek yakın nokta: {len(sensors) - limit} adet"))
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
            _bullet(f"Üst seviye/profil: {_adj(lookup.get('upper_air_profile'))}"),
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

    def _rounding_alarm_lines(
        self,
        analysis: ForecastAnalysis,
        metar: METARNormalized | None,
        taf: TAFNormalized | None,
    ) -> list[str]:
        reference_c = metar.temperature_c if metar is not None else analysis.final_tmax_c
        if reference_c is None:
            return [
                _bullet("Market kaderi 0.5°C yuvarlama sınırlarında; anlık/final sıcaklık verisi yok."),
                _bullet("Veri Kaynağı Kritik Eşikleri: canlı METAR gelince hesaplanacak."),
                _bullet("Kritik Saat Kırılımı (13:30 - 15:30): METAR/TAF verisi yok."),
            ]

        market_boundary_c = _next_market_rounding_boundary_c(reference_c)
        current_f = _c_to_f(reference_c)
        next_f = math.floor(current_f) + 1
        next_f_c = _f_to_c(next_f)
        delta_to_next_f_c = max(0.0, next_f_c - reference_c)
        rounded_c = _settlement_integer_from_reported_temp(market_boundary_c)
        return [
            _bullet(
                f"Marketin kaderi {market_boundary_c:.1f}°C sınırında. "
                f"{market_boundary_c:.1f}°C vurursa sistem bunu {rounded_c}°C tescil eder."
            ),
            _bullet(f"{market_boundary_c:.1f}°C = {rounded_c}°C; pozisyon aslında tam sayı değil, yarım derece eşiğine oynar."),
            "📊 Veri Kaynağı Kritik Eşikleri:",
            _bullet(f"Anlık değer: {reference_c:.1f}°C ({current_f:.0f}°F)"),
            _bullet(
                f"Sonraki Wunderground tam-F eşiği: {next_f:.0f}°F "
                f"({_fmt_floor_2(next_f_c)}°C) için +{_fmt_floor_2(delta_to_next_f_c)}°C gerekiyor."
            ),
            "🎯 Kritik Saat Kırılımı (13:30 - 15:30):",
            *self._critical_hour_lines(analysis.target_date, metar, taf),
        ]

    def _critical_hour_lines(
        self,
        target_date: date,
        metar: METARNormalized | None,
        taf: TAFNormalized | None,
    ) -> list[str]:
        period = _taf_period_for_window(taf, target_date, self.tz)
        wind_direction = period.wind_direction_deg if period and period.wind_direction_deg is not None else (metar.wind_direction_deg if metar else None)
        wind_speed = period.wind_speed_kt if period and period.wind_speed_kt is not None else (metar.wind_speed_kt if metar else None)
        clouds = period.clouds if period and period.clouds else (metar.cloud_layers if metar else [])
        return [
            _bullet(f"Pik saat rüzgâr tahmini: {_format_wind(wind_direction, wind_speed)} -> {_wind_thermal_effect(wind_direction)}"),
            _bullet(f"Gökyüzü kapalılığı (Oktas): {_cloud_cover_alarm_label(clouds)}"),
            _bullet(f"METAR trend analizi: {_metar_trend_label(metar)}"),
        ]

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

    def _advanced_signal_lines(self, bundle: ModelBundle) -> list[str]:
        adjustment = calculate_upper_air_profile_adjustment(bundle.forecasts)
        inputs = adjustment.inputs
        soil_moisture = _mean(_bundle_values(bundle, "soil_moisture_0_to_1cm_m3m3"))
        soil_temperature = _mean(_bundle_values(bundle, "soil_temperature_0cm_c"))
        lines = [
            _bullet(f"Üst seviye/profil: {adjustment.summary} (tmax etkisi {adjustment.value_c:+.1f}°C)"),
            _bullet(f"500 hPa yükseklik: {_fmt_m(inputs.get('midday_500hpa_height_m'))}"),
            _bullet(f"Jet akımı: {_fmt_kt(inputs.get('max_250hpa_wind_kt'))}"),
            _bullet(f"Enversiyon: {_fmt_signed_c(inputs.get('morning_inversion_strength_c'))}"),
            _bullet(
                "Konveksiyon/nem: "
                f"CAPE {_fmt_num(inputs.get('max_cape_jkg'))} J/kg, "
                f"700 hPa nem {_fmt_pct_from_whole(inputs.get('midday_700hpa_relative_humidity_pct'))}"
            ),
            _bullet(f"Zemin: toprak nemi {_fmt_soil_moisture(soil_moisture)}, yüzey {_fmt_c(soil_temperature)}"),
            _bullet("Okyanus/SST/ENSO sinyalleri Ankara günlük Tmax için doğrudan değil; mevsimsel arka plan olarak izlenmeli."),
        ]
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


def _compact_source_summary(snapshot: AviationSourceSnapshot) -> str:
    pieces = [snapshot.title, *snapshot.summary_lines[:2]]
    text = " | ".join(piece.strip() for piece in pieces if piece and piece.strip())
    text = " ".join(text.split())
    if len(text) > 220:
        return f"{text[:217]}..."
    return text or snapshot.kind


def _aviation_source_snapshot_key(snapshot: AviationSourceSnapshot) -> str:
    return f"telegram:aviation-source:{snapshot.station}:{snapshot.source}:{snapshot.kind}:{snapshot.fingerprint}"


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


def _fmt_integer_c(value: int | None) -> str:
    return f"{value}°C" if value is not None else "veri yok"


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


def _fmt_mm(value: float | int | None) -> str:
    return f"{float(value):.1f} mm" if value is not None else "veri yok"


def _fmt_m(value: object | None) -> str:
    if value is None:
        return "veri yok"
    return f"{float(value):,.0f} m"


def _fmt_kt(value: object | None) -> str:
    if value is None:
        return "veri yok"
    return f"{float(value):.0f} kt"


def _fmt_signed_c(value: object | None) -> str:
    if value is None:
        return "veri yok"
    return f"{float(value):+.1f}°C"


def _fmt_pct_from_whole(value: object | None) -> str:
    if value is None:
        return "veri yok"
    return f"%{float(value):.0f}"


def _fmt_soil_moisture(value: float | None) -> str:
    if value is None:
        return "veri yok"
    return f"{value:.2f} m³/m³"


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


def _forecast_market_candidate(analysis: ForecastAnalysis, market: MarketSnapshot | None) -> tuple[str, float | None, float | None, float | None] | None:
    if market is None or not market.valid_for_target:
        return None
    settlement = _nearest_settlement_integer(analysis.final_tmax_c)
    matching = None
    if settlement is not None:
        for outcome in market.outcomes:
            if _bracket_integer(outcome.bracket) == settlement:
                matching = outcome
                break
    if matching is None:
        candidate = _best_market_candidate(analysis, market)
        if candidate is None:
            return None
        edge, bracket, fair, implied = candidate
        return bracket, fair, implied, edge
    fair = analysis.fair_probabilities.get(matching.bracket)
    implied = matching.implied_probability
    edge = fair - implied if fair is not None and implied is not None else None
    return matching.bracket, fair, implied, edge


def _bracket_integer(bracket: str) -> int | None:
    match = __import__("re").search(r"-?\d+", bracket)
    return int(match.group(0)) if match else None


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


def _observed_peak_c(rows: list[dict[str, Any]], metar: METARNormalized | None) -> float | None:
    values: list[float] = []
    for row in rows:
        for key in ("tmpc", "temperature_c", "temp_c"):
            value = _safe_float(row.get(key))
            if value is not None:
                values.append(value)
                break
    if metar is not None:
        values.append(metar.temperature_c)
    return max(values) if values else None


def _remaining_warming_line(analysis: ForecastAnalysis, observed_peak_c: float | None) -> str:
    if analysis.final_tmax_c is None or observed_peak_c is None:
        return "Kalan ısınma payı: veri yok"
    remaining = max(0.0, analysis.final_tmax_c - observed_peak_c)
    return f"Kalan ısınma payı: {remaining:.1f}°C"


def _compact_nearby_sensor_lines(sensors: list[NearbySensorSnapshot], metar: METARNormalized | None) -> list[str]:
    if not sensors:
        return ["Yakın sensörler: veri yok"]
    parts = []
    for sensor in sensors[:3]:
        delta = ""
        if metar is not None and sensor.temperature_c is not None:
            delta = f" ({_fmt_signed_c(sensor.temperature_c - metar.temperature_c)})"
        parts.append(f"{sensor.name} {_fmt_c(sensor.temperature_c)}{delta}")
    suffix = f" +{len(sensors) - 3}" if len(sensors) > 3 else ""
    return [f"Yakın sensörler: {' · '.join(parts)}{suffix}"]


def _rounding_distance_line(final_tmax_c: float | None) -> str:
    if final_tmax_c is None:
        return "Yuvarlama sınırı: veri yok"
    settlement = _settlement_integer_from_reported_temp(final_tmax_c)
    lower_boundary = settlement - 0.5
    upper_boundary = settlement + 0.5
    down_delta = final_tmax_c - lower_boundary
    up_delta = upper_boundary - final_tmax_c
    if down_delta <= up_delta:
        return f"Yuvarlama sınırı: {settlement - 1}°C'ye düşmek için -{down_delta:.1f}°C pay var"
    return f"Yuvarlama sınırı: {settlement + 1}°C için +{up_delta:.1f}°C gerekir"


def _hourly_market_line(analysis: ForecastAnalysis, market: MarketSnapshot | None) -> str:
    candidate = _forecast_market_candidate(analysis, market)
    if candidate is None:
        return "Polymarket: market/fiyat verisi yok"
    bracket, fair, implied, edge = candidate
    parts = [bracket]
    if implied is not None:
        parts.append(f"piyasa {_fmt_cents(implied)}")
    if fair is not None:
        parts.append(f"bot fair {_fmt_pct(fair)}")
    if edge is not None:
        parts.append(f"edge {_fmt_pp(edge)}")
    return f"Polymarket: {' · '.join(parts)}"


def _hourly_decision_line(analysis: ForecastAnalysis, market: MarketSnapshot | None, settings: Settings) -> str:
    if analysis.final_tmax_c is None:
        return "Karar: BET YOK — tahmin verisi eksik"
    boundary = _boundary_risk(analysis)
    if analysis.confidence_score < settings.telegram_hourly_forecast_no_bet_confidence:
        return "Karar: BET YOK — güven çok düşük"
    if analysis.model_spread_c is not None and analysis.model_spread_c >= settings.telegram_hourly_forecast_model_spread_c:
        return f"Karar: BEKLE — model farkı {analysis.model_spread_c:.1f}°C"
    candidate = _forecast_market_candidate(analysis, market)
    settlement = _nearest_settlement_integer(analysis.final_tmax_c)
    if candidate is None:
        return f"Karar: {settlement}°C ana senaryo — piyasa edge verisi yok"
    bracket, fair, implied, edge = candidate
    min_edge = settings.telegram_hourly_forecast_min_edge_pp / 100.0
    if edge is None or fair is None or implied is None:
        return f"Karar: {bracket} ana senaryo — fiyat/fair eksik"
    if edge >= min_edge and analysis.confidence_score >= settings.telegram_hourly_forecast_min_confidence and boundary != "YÜKSEK":
        return f"Karar: {bracket} güçlü aday — manuel onay gerekir"
    if boundary == "YÜKSEK":
        return f"Karar: {bracket} ana senaryo ama HEDGE/BEKLE — sınır riski yüksek"
    return f"Karar: {bracket} ana senaryo — edge eşiğin altında"


def _next_hour_label(report_time: datetime) -> str:
    next_hour = (report_time + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return f"{next_hour:%H:%M}"


def _expected_profit_usd(stake_usd: float, fair_probability: float, yes_price: float) -> float | None:
    if yes_price <= 0.0 or yes_price >= 1.0:
        return None
    shares = stake_usd / yes_price
    return fair_probability * shares - stake_usd


def _settlement_integer_from_reported_temp(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def _next_market_rounding_boundary_c(value_c: float) -> float:
    integer_part = math.floor(value_c)
    boundary = integer_part + 0.5
    if value_c < boundary:
        return boundary
    return integer_part + 1.5


def _c_to_f(value_c: float) -> float:
    return value_c * 9 / 5 + 32


def _f_to_c(value_f: float) -> float:
    return (value_f - 32) * 5 / 9


def _fmt_floor_2(value: float) -> str:
    return f"{math.floor(value * 100) / 100:.2f}"


def _taf_period_for_window(taf: TAFNormalized | None, target_date: date, tz: ZoneInfo) -> object | None:
    if taf is None:
        return None
    window_start = datetime.combine(target_date, time(13, 30), tzinfo=tz)
    window_end = datetime.combine(target_date, time(15, 30), tzinfo=tz)
    for period in taf.periods:
        period_start = period.time_from.astimezone(tz)
        period_end = period.time_to.astimezone(tz)
        if period_start <= window_end and period_end >= window_start:
            return period
    return None


def _format_wind(direction_deg: int | None, speed_kt: float | None) -> str:
    direction = f"{direction_deg:03d}°" if direction_deg is not None else "VRB"
    speed = f"{speed_kt:.0f} KT" if speed_kt is not None else "veri yok"
    return f"{direction} / {speed}"


def _wind_thermal_effect(direction_deg: int | None) -> str:
    if direction_deg is None:
        return "rüzgâr yönü belirsiz"
    if direction_deg <= 80 or direction_deg >= 320:
        return "kuzeyli akış ısınmayı baskılayacak"
    if 240 <= direction_deg <= 300:
        return "batı rüzgârı pist/asfalt ısısını +0.4°C yukarı taşıyabilir"
    if 160 <= direction_deg <= 230:
        return "güneybatılı akış yukarı risk yaratır"
    return "termal etki nötr"


def _cloud_cover_alarm_label(clouds: list[dict]) -> str:
    if not clouds:
        return "veri yok"
    covers = [str(cloud.get("cover") or "").upper() for cloud in clouds]
    primary = next((cover for cover in covers if cover), "veri yok")
    if any(cover in {"BKN", "OVC"} for cover in covers):
        return f"{primary} (güneşlenme kısıtlı)"
    if any(cover in {"FEW", "SCT"} for cover in covers):
        return f"{primary} (güneşlenme açık)"
    return primary


def _metar_trend_label(metar: METARNormalized | None) -> str:
    if metar is None or not metar.raw_text:
        return "veri yok"
    raw = metar.raw_text.upper()
    if "NOSIG" in raw:
        return "NOSIG (değişim beklenmiyor)"
    if "TEMPO" in raw or "BECMG" in raw:
        return "değişim sinyali var"
    return "trend kodu yok"


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


def _metar_wind_text(metar: METARNormalized) -> str:
    direction = f"{metar.wind_direction_deg:03d}°" if metar.wind_direction_deg is not None else "VRB"
    gust = f" G{metar.wind_gust_kt:.0f}" if metar.wind_gust_kt is not None else ""
    return f"{direction}/{metar.wind_speed_kt:.0f}{gust} KT"


def _metar_source_lines(metar: METARNormalized) -> list[str]:
    station = metar.station.strip().upper()
    return [
        _bullet(f"AviationWeather: https://aviationweather.gov/api/data/metar?ids={station}&format=json"),
        _bullet(f"IEM ASOS: https://mesonet.agron.iastate.edu/sites/site.php?station={station}&network=TR__ASOS"),
    ]


def _format_visibility_m(value: int | None) -> str:
    if value is None:
        return "veri yok"
    if value >= 9999:
        return f"10 km+ ({value} m)"
    return f"{value} m"


def _metar_weather_text(metar: METARNormalized) -> str:
    raw = metar.raw_json or {}
    wx = raw.get("wxString") or raw.get("flight_category")
    if wx:
        return str(wx)
    conditions = raw.get("conditions")
    if isinstance(conditions, list) and conditions:
        parts = []
        for item in conditions:
            if isinstance(item, dict):
                parts.append(str(item.get("code") or item.get("text") or item.get("summary") or item))
            else:
                parts.append(str(item))
        return ", ".join(parts)
    return "yok"


def _metar_extra_sensor_lines(metar: METARNormalized) -> list[str]:
    raw = metar.raw_json or {}
    groups = [
        (
            "Ek sensör",
            [
                ("tip", raw.get("metarType")),
                ("SLP", raw.get("slp")),
                ("basınç trend", raw.get("presTend")),
                ("dikey görüş", raw.get("vertVis")),
                ("QC", raw.get("qcField")),
            ],
        ),
        (
            "Yağış/kar",
            [
                ("anlık", raw.get("precip")),
                ("3s", raw.get("pcp3hr")),
                ("6s", raw.get("pcp6hr")),
                ("24s", raw.get("pcp24hr")),
                ("kar", raw.get("snow")),
            ],
        ),
        (
            "Sıcaklık uçları",
            [
                ("maxT", raw.get("maxT")),
                ("minT", raw.get("minT")),
                ("maxT24", raw.get("maxT24")),
                ("minT24", raw.get("minT24")),
            ],
        ),
    ]
    lines = []
    for label, values in groups:
        parts = [f"{key}={value}" for key, value in values if value not in (None, "", "M")]
        if parts:
            lines.append(f"{label}: {', '.join(parts)}")
    return lines or ["Ek sensör: normalized METAR alanları dışında ek ham sensör yok"]


def _bundle_values(bundle: ModelBundle, field: str) -> list[float]:
    values = []
    for forecast in bundle.available_forecasts:
        for point in forecast.midday_points:
            value = getattr(point, field)
            if value is not None:
                values.append(float(value))
    return values


def _mean(values: list[float]) -> float | None:
    return mean(values) if values else None


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


def _adj(adjustment: object | None) -> str:
    if adjustment is None:
        return "veri yok"
    return f"{adjustment.summary} ({adjustment.value_c:+.1f}°C)"
