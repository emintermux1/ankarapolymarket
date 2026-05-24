from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from src.data_sources.schemas import (
    METARNormalized,
    ModelForecast,
    ModelHourlyPoint,
    NowcastingSignal,
    TAFNormalized,
)

RUNWAY_HEADINGS_DEG = (35.0, 215.0)


def calculate_nowcasting_signals(
    *,
    metar: METARNormalized | None,
    taf: TAFNormalized | None,
    forecasts: list[ModelForecast],
    recent_observations: list[METARNormalized] | None,
    target_date: date,
    report_timezone: str,
    ltac_elevation_m: int,
) -> list[NowcastingSignal]:
    tz = ZoneInfo(report_timezone)
    now = datetime.now(tz)
    observations = _merge_observations(recent_observations or [], metar)
    available_forecasts = [forecast for forecast in forecasts if forecast.available]
    return [
        _cloud_shadow_signal(available_forecasts, now, tz),
        _temperature_spike_signal(observations, available_forecasts, tz),
        _peak_window_signal(available_forecasts, target_date, tz),
        _city_center_bias_signal(metar, ltac_elevation_m),
        _microburst_signal(metar, taf, available_forecasts, now, tz),
        _wind_shift_signal(metar, taf, available_forecasts, now, tz),
        _radar_cell_signal(taf, available_forecasts, now, tz),
        _temperature_momentum_signal(observations),
    ]


def _cloud_shadow_signal(forecasts: list[ModelForecast], now: datetime, tz: ZoneInfo) -> NowcastingSignal:
    label = "Bulut Gölge Simülasyonu"
    series = _cloud_series(forecasts, tz)
    if not series:
        return _unavailable("cloud_shadow", label, "uydu görüntüsü/radar adaptörü ve model bulut verisi yok")

    base_time, base_cover = min(series, key=lambda item: abs((item[0] - now).total_seconds()))
    horizon = now + timedelta(hours=6)
    candidates = [
        (time, cover)
        for time, cover in series
        if now <= time <= horizon and cover >= 60.0 and cover - base_cover >= 20.0
    ]
    if not candidates:
        return NowcastingSignal(
            name="cloud_shadow",
            label=label,
            state="Belirgin gölge sinyali yok",
            severity="low",
            summary=(
                "uydu görüntüsü bağlı değil; model bulut proxy’si önümüzdeki 6 saatte "
                "Esenboğa üstüne keskin gölge geçişi göstermiyor"
            ),
            inputs={"base_cloud_cover_pct": round(base_cover, 1), "base_time": base_time.isoformat()},
        )

    arrival_time, cover = candidates[0]
    arrival_minutes = _round_to_step(max(0.0, (arrival_time - now).total_seconds() / 60.0), 5)
    wind = _mean_wind_near(forecasts, arrival_time, tz)
    if wind["direction_deg"] is not None:
        movement_dir = (wind["direction_deg"] + 180.0) % 360.0
        motion = f", bulut hareket yönü yaklaşık {_fmt_deg(movement_dir)}"
    else:
        movement_dir = None
        motion = ""
    severity = "medium" if arrival_minutes <= 120 else "low"
    return NowcastingSignal(
        name="cloud_shadow",
        label=label,
        state=f"~{arrival_minutes:.0f} dk",
        severity=severity,
        summary=(
            "uydu görüntüsü bağlı değil; model bulut adveksiyon proxy’si "
            f"Esenboğa üstünde %{cover:.0f} bulutlanmayı yaklaşık {arrival_minutes:.0f} dk sonra veriyor{motion}"
        ),
        inputs={
            "arrival_time": arrival_time.isoformat(),
            "arrival_minutes": arrival_minutes,
            "base_cloud_cover_pct": round(base_cover, 1),
            "arrival_cloud_cover_pct": round(cover, 1),
            "movement_direction_deg": round(movement_dir, 1) if movement_dir is not None else None,
        },
    )


def _temperature_spike_signal(
    observations: list[METARNormalized],
    forecasts: list[ModelForecast],
    tz: ZoneInfo,
) -> NowcastingSignal:
    label = "Ani Spike Riski"
    window = _observation_window(observations, minutes=120)
    if window is None:
        return _unavailable("temperature_spike", label, "son 2 saat için en az iki METAR gözlemi yok")

    first, latest, elapsed_minutes = window
    delta = latest.temperature_c - first.temperature_c
    rate = delta / (elapsed_minutes / 60.0)
    expected_rate = _expected_model_rate(forecasts, _observation_time(first), _observation_time(latest), tz)
    excess = rate - expected_rate if expected_rate is not None else None
    if rate >= 1.6 and (excess is None or excess >= 0.7):
        state = "Yüksek"
        severity = "high"
    elif rate >= 1.0 and (excess is None or excess >= 0.3):
        state = "Orta"
        severity = "medium"
    else:
        state = "Düşük"
        severity = "low"

    normal = f", model normalinden {excess:+.1f}°C/saat hızlı" if excess is not None else ", model normali yok"
    return NowcastingSignal(
        name="temperature_spike",
        label=label,
        state=state,
        severity=severity,
        summary=f"son {elapsed_minutes:.0f} dk {_fmt_delta(delta)} değişim ({rate:+.1f}°C/saat{normal})",
        inputs={
            "window_minutes": round(elapsed_minutes, 1),
            "temperature_delta_c": round(delta, 2),
            "observed_rate_c_per_hour": round(rate, 2),
            "expected_rate_c_per_hour": round(expected_rate, 2) if expected_rate is not None else None,
            "excess_rate_c_per_hour": round(excess, 2) if excess is not None else None,
        },
    )


def _peak_window_signal(forecasts: list[ModelForecast], target_date: date, tz: ZoneInfo) -> NowcastingSignal:
    label = "Peak Window"
    peaks: list[tuple[str, datetime]] = []
    for forecast in forecasts:
        peak_time = _forecast_peak_time(forecast, target_date, tz)
        if peak_time is not None:
            peaks.append((forecast.model, peak_time))
    if not peaks:
        return _unavailable("peak_window", label, "saatlik model sıcaklığı yok")

    minutes = sorted(_minutes_since_midnight(time) for _, time in peaks)
    if len(minutes) >= 3:
        start_minute = _percentile(minutes, 25) - 20.0
        end_minute = _percentile(minutes, 75) + 20.0
    elif len(minutes) == 2:
        start_minute = minutes[0] - 20.0
        end_minute = minutes[1] + 20.0
    else:
        start_minute = minutes[0] - 45.0
        end_minute = minutes[0] + 45.0

    start_minute = max(0.0, min(1439.0, start_minute))
    end_minute = max(start_minute + 15.0, min(1439.0, end_minute))
    start_label = _fmt_clock_from_minutes(start_minute)
    end_label = _fmt_clock_from_minutes(end_minute)
    model_peaks = ", ".join(f"{_display_model_name(model)} {_fmt_hhmm(time)}" for model, time in peaks[:4])
    return NowcastingSignal(
        name="peak_window",
        label=label,
        state=f"{start_label} - {end_label}",
        severity="info",
        summary=f"bugünkü maksimum için model tepe penceresi {start_label} - {end_label}; model pikleri: {model_peaks}",
        inputs={
            "window_start": start_label,
            "window_end": end_label,
            "model_peak_times": {model: _fmt_hhmm(time) for model, time in peaks},
        },
    )


def _city_center_bias_signal(metar: METARNormalized | None, ltac_elevation_m: int) -> NowcastingSignal:
    label = "Şehir Merkezi Bias"
    prefix = f"LTAC son gözlem {metar.temperature_c:.1f}°C; " if metar is not None else ""
    return NowcastingSignal(
        name="city_center_bias",
        label=label,
        state="Merkez > LTAC makul",
        severity="info",
        summary=(
            f"{prefix}Ankara merkez Esenboğa’dan sıcak kalabilir: şehir ısı adası + vadi/zemin etkisi, "
            f"Esenboğa ise {ltac_elevation_m} m açık plato/kırsal istasyon; 3-5°C fark fiziksel olarak normal"
        ),
        inputs={"ltac_elevation_m": ltac_elevation_m},
    )


def _microburst_signal(
    metar: METARNormalized | None,
    taf: TAFNormalized | None,
    forecasts: list[ModelForecast],
    now: datetime,
    tz: ZoneInfo,
) -> NowcastingSignal:
    label = "Microburst / Downburst Riski"
    points = _future_points(forecasts, now, tz, hours=6)
    max_cape = _max_optional(point.cape_jkg for point in points)
    max_precip = _max_optional(point.precipitation_mm for point in points)
    max_cloud_base = _max_optional(point.cloud_base_m for point in points)
    temp_dew_spread = metar.temperature_c - metar.dew_point_c if metar is not None else None
    gust_spread = None
    if metar and metar.wind_gust_kt is not None:
        gust_spread = metar.wind_gust_kt - metar.wind_speed_kt
    taf_convective = _taf_convective(taf, now, tz, hours=6)
    has_input = any(value is not None for value in (max_cape, max_precip, max_cloud_base, temp_dew_spread, gust_spread)) or taf_convective
    if not has_input:
        return _unavailable("microburst", label, "CAPE/yağış/bulut tabanı/TAF konveksiyon verisi yok")

    score = 0
    if max_cape is not None:
        score += 2 if max_cape >= 1000 else 1 if max_cape >= 400 else 0
    if max_precip is not None:
        score += 2 if max_precip >= 1.0 else 1 if max_precip >= 0.2 else 0
    if temp_dew_spread is not None and max_cape is not None and temp_dew_spread >= 14 and max_cape >= 400:
        score += 1
    if max_cloud_base is not None and max_cloud_base >= 1800 and max_cape is not None and max_cape >= 400:
        score += 1
    if gust_spread is not None and gust_spread >= 12:
        score += 1
    if taf_convective:
        score += 2

    if score >= 5:
        state = "Yüksek"
        severity = "high"
    elif score >= 3:
        state = "Orta"
        severity = "medium"
    else:
        state = "Düşük"
        severity = "low"
    return NowcastingSignal(
        name="microburst",
        label=label,
        state=state,
        severity=severity,
        summary=(
            f"CAPE {_fmt_optional(max_cape, '.0f')} J/kg, yağış {_fmt_optional(max_precip, '.1f')} mm/s, "
            f"T-Td {_fmt_optional(temp_dew_spread, '.1f')}°C, TAF TS/CB {'var' if taf_convective else 'yok'}"
        ),
        inputs={
            "score": score,
            "max_cape_jkg": round(max_cape, 1) if max_cape is not None else None,
            "max_precipitation_mm": round(max_precip, 2) if max_precip is not None else None,
            "max_cloud_base_m": round(max_cloud_base, 1) if max_cloud_base is not None else None,
            "temperature_dewpoint_spread_c": round(temp_dew_spread, 1) if temp_dew_spread is not None else None,
            "gust_spread_kt": round(gust_spread, 1) if gust_spread is not None else None,
            "taf_convective": taf_convective,
        },
    )


def _wind_shift_signal(
    metar: METARNormalized | None,
    taf: TAFNormalized | None,
    forecasts: list[ModelForecast],
    now: datetime,
    tz: ZoneInfo,
) -> NowcastingSignal:
    label = "Runway Wind Shift Alert"
    entries = _wind_entries(metar, taf, forecasts, now, tz)
    if len(entries) < 2:
        return _unavailable("runway_wind_shift", label, "önümüzdeki 6 saat için yeterli rüzgâr yönü yok")

    max_shift = 0.0
    max_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
    for previous, current in zip(entries, entries[1:]):
        shift = _angular_diff(previous["direction_deg"], current["direction_deg"])
        if shift > max_shift:
            max_shift = shift
            max_pair = (previous, current)
    speeds = [entry["speed_kt"] for entry in entries if entry["speed_kt"] is not None]
    max_speed = max(speeds) if speeds else None
    crosswinds = [
        _runway_crosswind_component(entry["direction_deg"], entry["speed_kt"])
        for entry in entries
        if entry["speed_kt"] is not None
    ]
    max_crosswind = max(crosswinds) if crosswinds else None
    if max_shift >= 60.0 and (max_speed is None or max_speed >= 8.0):
        state = "Yüksek"
        severity = "high"
    elif max_shift >= 35.0 and (max_speed is None or max_speed >= 6.0):
        state = "Orta"
        severity = "medium"
    else:
        state = "Düşük"
        severity = "low"
    pair_text = ""
    if max_pair is not None:
        pair_text = (
            f" ({max_pair[0]['source']} {_fmt_hhmm(max_pair[0]['time'])} "
            f"{_fmt_deg(max_pair[0]['direction_deg'])} → {max_pair[1]['source']} "
            f"{_fmt_hhmm(max_pair[1]['time'])} {_fmt_deg(max_pair[1]['direction_deg'])})"
        )
    crosswind_text = f", 03/21 pist yan bileşeni max ~{max_crosswind:.0f} kt" if max_crosswind is not None else ""
    return NowcastingSignal(
        name="runway_wind_shift",
        label=label,
        state=state,
        severity=severity,
        summary=f"önümüzdeki 6 saatte en büyük yön kırılması {max_shift:.0f}°{pair_text}{crosswind_text}",
        inputs={
            "max_shift_deg": round(max_shift, 1),
            "max_speed_kt": round(max_speed, 1) if max_speed is not None else None,
            "max_crosswind_component_kt": round(max_crosswind, 1) if max_crosswind is not None else None,
        },
    )


def _radar_cell_signal(
    taf: TAFNormalized | None,
    forecasts: list[ModelForecast],
    now: datetime,
    tz: ZoneInfo,
) -> NowcastingSignal:
    label = "Radar Hücre Takibi"
    points = _future_points(forecasts, now, tz, hours=4)
    taf_convective = _taf_convective(taf, now, tz, hours=4)
    series = _convective_intensity_series(points, tz)
    if not series and not taf_convective:
        return _unavailable("radar_cell_tracking", label, "radar adaptörü yok; model/TAF konveksiyon proxy’si de yok")

    max_intensity = max((value for _, value in series), default=0.0)
    if max_intensity < 0.7 and not taf_convective:
        return NowcastingSignal(
            name="radar_cell_tracking",
            label=label,
            state="Belirgin hücre yok",
            severity="low",
            summary="radar adaptörü yok; model/TAF proxy’si Çubuk yönünde aktif konvektif hücre sinyali vermiyor",
            inputs={"max_proxy_intensity": round(max_intensity, 2)},
        )

    trend = (series[-1][1] - series[0][1]) if len(series) >= 2 else 0.0
    if trend > 0.35:
        strength = "güçleniyor"
    elif trend < -0.25:
        strength = "dağılıyor"
    else:
        strength = "stabil"

    wind = _mean_wind_near(forecasts, now + timedelta(hours=2), tz)
    movement_dir = (wind["direction_deg"] + 180.0) % 360.0 if wind["direction_deg"] is not None else None
    if movement_dir is None:
        impact = "rota belirsiz"
    elif _in_sector(movement_dir, 135.0, 225.0):
        impact = "Esenboğa hattına inebilir"
    elif _in_sector(movement_dir, 110.0, 250.0):
        impact = "Esenboğa yakınından geçebilir"
    else:
        impact = "Esenboğa’yı ıskalayabilir"

    severity = "medium" if impact != "Esenboğa’yı ıskalayabilir" and strength != "dağılıyor" else "low"
    if taf_convective and severity == "medium":
        severity = "high"
    return NowcastingSignal(
        name="radar_cell_tracking",
        label=label,
        state=f"Proxy: {strength}, {impact}",
        severity=severity,
        summary=(
            "radar adaptörü yok; model/TAF proxy’sine göre Çubuk yönlü hücre "
            f"{strength}, {impact.lower()}"
            + (f", hareket yönü {_fmt_deg(movement_dir)}" if movement_dir is not None else "")
        ),
        inputs={
            "trend": round(trend, 2),
            "max_proxy_intensity": round(max_intensity, 2),
            "movement_direction_deg": round(movement_dir, 1) if movement_dir is not None else None,
            "taf_convective": taf_convective,
        },
    )


def _temperature_momentum_signal(observations: list[METARNormalized]) -> NowcastingSignal:
    label = "Sıcaklık Momentum"
    window = _observation_window(observations, minutes=90)
    if window is None:
        return _unavailable("temperature_momentum", label, "son 90 dakika için en az iki METAR gözlemi yok")

    first, latest, elapsed_minutes = window
    delta = latest.temperature_c - first.temperature_c
    rate = delta / (elapsed_minutes / 60.0)
    if delta >= 1.5 or rate >= 1.2:
        state = "Güçlü momentum"
        severity = "high"
    elif delta >= 0.7 or rate >= 0.6:
        state = "Pozitif momentum"
        severity = "medium"
    elif delta <= -0.7 or rate <= -0.6:
        state = "Soğuma momentum"
        severity = "medium"
    else:
        state = "Zayıf/nötr"
        severity = "low"
    return NowcastingSignal(
        name="temperature_momentum",
        label=label,
        state=state,
        severity=severity,
        summary=f"son {elapsed_minutes:.0f} dk {_fmt_delta(delta)} değişim ({rate:+.1f}°C/saat)",
        inputs={
            "window_minutes": round(elapsed_minutes, 1),
            "temperature_delta_c": round(delta, 2),
            "rate_c_per_hour": round(rate, 2),
        },
    )


def _merge_observations(
    observations: list[METARNormalized],
    metar: METARNormalized | None,
) -> list[METARNormalized]:
    by_time: dict[datetime, METARNormalized] = {}
    for observation in observations:
        by_time[_observation_time(observation)] = observation
    if metar is not None:
        by_time[_observation_time(metar)] = metar
    return [by_time[key] for key in sorted(by_time)]


def _observation_window(
    observations: list[METARNormalized],
    *,
    minutes: int,
) -> tuple[METARNormalized, METARNormalized, float] | None:
    if len(observations) < 2:
        return None
    latest = observations[-1]
    latest_time = _observation_time(latest)
    start_time = latest_time - timedelta(minutes=minutes)
    candidates = [item for item in observations if start_time <= _observation_time(item) <= latest_time]
    if len(candidates) < 2:
        return None
    first = candidates[0]
    elapsed_minutes = (_observation_time(latest) - _observation_time(first)).total_seconds() / 60.0
    if elapsed_minutes < 30.0:
        return None
    return first, latest, elapsed_minutes


def _observation_time(observation: METARNormalized) -> datetime:
    observed = observation.observation_time
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc)


def _expected_model_rate(
    forecasts: list[ModelForecast],
    start_time_utc: datetime,
    end_time_utc: datetime,
    tz: ZoneInfo,
) -> float | None:
    elapsed_hours = (end_time_utc - start_time_utc).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return None
    start_local = start_time_utc.astimezone(tz)
    end_local = end_time_utc.astimezone(tz)
    rates = []
    for forecast in forecasts:
        start_temp = _nearest_temperature(forecast, start_local, tz)
        end_temp = _nearest_temperature(forecast, end_local, tz)
        if start_temp is not None and end_temp is not None:
            rates.append((end_temp - start_temp) / elapsed_hours)
    return mean(rates) if rates else None


def _nearest_temperature(forecast: ModelForecast, when: datetime, tz: ZoneInfo) -> float | None:
    candidates = [
        (_to_local(point.time, tz), point.temperature_2m_c)
        for point in forecast.hourly
        if point.temperature_2m_c is not None
    ]
    if not candidates:
        return None
    nearest_time, value = min(candidates, key=lambda item: abs((item[0] - when).total_seconds()))
    if abs((nearest_time - when).total_seconds()) > 2700:
        return None
    return value


def _forecast_peak_time(forecast: ModelForecast, target_date: date, tz: ZoneInfo) -> datetime | None:
    points = [
        (_to_local(point.time, tz), point.temperature_2m_c)
        for point in forecast.hourly
        if point.temperature_2m_c is not None and _to_local(point.time, tz).date() == target_date
    ]
    if not points:
        return None
    max_idx = max(range(len(points)), key=lambda idx: points[idx][1])
    peak_time = points[max_idx][0]
    if 0 < max_idx < len(points) - 1:
        y0 = points[max_idx - 1][1]
        y1 = points[max_idx][1]
        y2 = points[max_idx + 1][1]
        denominator = y0 - 2.0 * y1 + y2
        if abs(denominator) > 0.001:
            offset = max(-0.75, min(0.75, 0.5 * (y0 - y2) / denominator))
            step_seconds = (points[max_idx + 1][0] - points[max_idx][0]).total_seconds()
            peak_time = peak_time + timedelta(seconds=offset * step_seconds)
    return peak_time


def _cloud_series(forecasts: list[ModelForecast], tz: ZoneInfo) -> list[tuple[datetime, float]]:
    buckets: dict[datetime, list[float]] = {}
    for forecast in forecasts:
        for point in forecast.hourly:
            cover = _cloud_cover(point)
            if cover is None:
                continue
            time = _hour_bucket(_to_local(point.time, tz))
            buckets.setdefault(time, []).append(cover)
    return sorted((time, mean(values)) for time, values in buckets.items() if values)


def _cloud_cover(point: ModelHourlyPoint) -> float | None:
    if point.cloud_cover_pct is not None:
        return point.cloud_cover_pct
    layers = [
        value
        for value in (point.cloud_cover_low_pct, point.cloud_cover_mid_pct, point.cloud_cover_high_pct)
        if value is not None
    ]
    return max(layers) if layers else None


def _future_points(forecasts: list[ModelForecast], now: datetime, tz: ZoneInfo, *, hours: int) -> list[ModelHourlyPoint]:
    horizon = now + timedelta(hours=hours)
    points = []
    for forecast in forecasts:
        for point in forecast.hourly:
            local_time = _to_local(point.time, tz)
            if now <= local_time <= horizon:
                points.append(point)
    return points


def _taf_convective(taf: TAFNormalized | None, now: datetime, tz: ZoneInfo, *, hours: int) -> bool:
    if taf is None:
        return False
    horizon = now + timedelta(hours=hours)
    tokens = ("TS", "TSRA", "SHRA", "CB")
    for period in taf.periods:
        starts = _to_local(period.time_from, tz)
        ends = _to_local(period.time_to, tz)
        if ends < now or starts > horizon:
            continue
        if period.weather and any(token in period.weather for token in tokens):
            return True
        if any(cloud.get("type") == "CB" for cloud in period.clouds):
            return True
    return False


def _wind_entries(
    metar: METARNormalized | None,
    taf: TAFNormalized | None,
    forecasts: list[ModelForecast],
    now: datetime,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    horizon = now + timedelta(hours=6)
    entries: list[dict[str, Any]] = []
    if metar is not None and metar.wind_direction_deg is not None:
        entries.append(
            {
                "time": _observation_time(metar).astimezone(tz),
                "direction_deg": float(metar.wind_direction_deg),
                "speed_kt": metar.wind_speed_kt,
                "source": "METAR",
            }
        )
    if taf is not None:
        for period in taf.periods:
            starts = _to_local(period.time_from, tz)
            ends = _to_local(period.time_to, tz)
            if ends < now or starts > horizon or period.wind_direction_deg is None:
                continue
            entries.append(
                {
                    "time": max(starts, now),
                    "direction_deg": float(period.wind_direction_deg),
                    "speed_kt": period.wind_speed_kt,
                    "source": "TAF",
                }
            )
    entries.extend(_model_wind_series(forecasts, now, tz, hours=6))
    return sorted(entries, key=lambda item: item["time"])


def _model_wind_series(
    forecasts: list[ModelForecast],
    now: datetime,
    tz: ZoneInfo,
    *,
    hours: int,
) -> list[dict[str, Any]]:
    buckets: dict[datetime, dict[str, list[float]]] = {}
    horizon = now + timedelta(hours=hours)
    for forecast in forecasts:
        for point in forecast.hourly:
            time = _to_local(point.time, tz)
            if now <= time <= horizon or abs((time - now).total_seconds()) <= 1800:
                wind_dir = point.wind_direction_10m_deg
                wind_speed = point.wind_speed_10m_kt
                if wind_dir is None:
                    continue
                bucket = buckets.setdefault(_hour_bucket(time), {"directions": [], "speeds": []})
                bucket["directions"].append(float(wind_dir))
                if wind_speed is not None:
                    bucket["speeds"].append(float(wind_speed))
    entries = []
    for time, values in sorted(buckets.items()):
        if not values["directions"]:
            continue
        entries.append(
            {
                "time": time,
                "direction_deg": _circular_mean(values["directions"]),
                "speed_kt": mean(values["speeds"]) if values["speeds"] else None,
                "source": "model",
            }
        )
    return entries


def _mean_wind_near(forecasts: list[ModelForecast], when: datetime, tz: ZoneInfo) -> dict[str, float | None]:
    directions = []
    speeds = []
    for forecast in forecasts:
        for point in forecast.hourly:
            local_time = _to_local(point.time, tz)
            if abs((local_time - when).total_seconds()) > 3600:
                continue
            wind_dir = point.wind_direction_850hpa_deg if point.wind_direction_850hpa_deg is not None else point.wind_direction_10m_deg
            wind_speed = point.wind_speed_850hpa_kt if point.wind_speed_850hpa_kt is not None else point.wind_speed_10m_kt
            if wind_dir is not None:
                directions.append(float(wind_dir))
            if wind_speed is not None:
                speeds.append(float(wind_speed))
    return {
        "direction_deg": _circular_mean(directions) if directions else None,
        "speed_kt": mean(speeds) if speeds else None,
    }


def _convective_intensity_series(points: list[ModelHourlyPoint], tz: ZoneInfo) -> list[tuple[datetime, float]]:
    buckets: dict[datetime, list[float]] = {}
    for point in points:
        intensity = _convective_intensity(point)
        if intensity is None:
            continue
        buckets.setdefault(_hour_bucket(_to_local(point.time, tz)), []).append(intensity)
    return sorted((time, mean(values)) for time, values in buckets.items() if values)


def _convective_intensity(point: ModelHourlyPoint) -> float | None:
    values = [point.cape_jkg, point.precipitation_mm, point.cloud_cover_low_pct, point.cloud_cover_mid_pct]
    if all(value is None for value in values):
        return None
    cape_component = min(2.0, (point.cape_jkg or 0.0) / 800.0)
    precip_component = min(2.0, (point.precipitation_mm or 0.0) * 1.5)
    cloud_component = max(point.cloud_cover_low_pct or 0.0, point.cloud_cover_mid_pct or 0.0) / 200.0
    return cape_component + precip_component + cloud_component


def _max_optional(values: Any) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return max(clean) if clean else None


def _to_local(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _hour_bucket(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _minutes_since_midnight(value: datetime) -> float:
    return value.hour * 60.0 + value.minute + value.second / 60.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("values cannot be empty")
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = (len(ordered) - 1) * (percentile / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _circular_mean(values: list[float]) -> float:
    sin_sum = sum(math.sin(math.radians(value)) for value in values)
    cos_sum = sum(math.cos(math.radians(value)) for value in values)
    return math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0


def _angular_diff(first: float, second: float) -> float:
    return abs((second - first + 180.0) % 360.0 - 180.0)


def _runway_crosswind_component(direction_deg: float, speed_kt: float) -> float:
    angle = min(_angular_diff(direction_deg, heading) for heading in RUNWAY_HEADINGS_DEG)
    return abs(speed_kt * math.sin(math.radians(angle)))


def _in_sector(direction_deg: float, start_deg: float, end_deg: float) -> bool:
    direction = direction_deg % 360.0
    start = start_deg % 360.0
    end = end_deg % 360.0
    if start <= end:
        return start <= direction <= end
    return direction >= start or direction <= end


def _round_to_step(value: float, step: int) -> float:
    return float(round(value / step) * step)


def _fmt_delta(value: float) -> str:
    return f"{value:+.1f}°C"


def _fmt_optional(value: float | None, spec: str) -> str:
    return format(value, spec) if value is not None else "veri yok"


def _fmt_deg(value: float) -> str:
    return f"{value:.0f}°"


def _fmt_hhmm(value: datetime) -> str:
    return f"{value:%H:%M}"


def _fmt_clock_from_minutes(value: float) -> str:
    rounded = int(_round_to_step(value, 5))
    hours = min(23, rounded // 60)
    minutes = min(55, rounded % 60)
    return f"{hours:02d}:{minutes:02d}"


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
    if "icon" in lowered:
        return "ICON"
    if "visual" in lowered:
        return "Visual Crossing"
    if "tomorrow" in lowered:
        return "Tomorrow.io"
    return model


def _unavailable(name: str, label: str, summary: str) -> NowcastingSignal:
    return NowcastingSignal(
        name=name,
        label=label,
        state="Veri yok",
        severity="unavailable",
        summary=summary,
    )
