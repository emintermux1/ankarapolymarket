from __future__ import annotations

import math
from statistics import mean

from src.data_sources.schemas import ForecastAdjustment, METARNormalized, ModelForecast, ModelHourlyPoint


def calculate_ai_effect_analysis(
    metar: METARNormalized | None,
    forecasts: list[ModelForecast],
) -> ForecastAdjustment:
    afternoon_points = [
        point
        for forecast in forecasts
        if forecast.available
        for point in forecast.hourly
        if 12 <= point.time.hour <= 18
    ]
    cape_values = [point.cape_jkg for point in afternoon_points if point.cape_jkg is not None]
    cin_values = [
        magnitude
        for point in afternoon_points
        if (magnitude := _cin_magnitude(point.convective_inhibition_jkg)) is not None
    ]
    wind_direction, wind_speed = _surface_wind(metar, afternoon_points)
    cape_max = max(cape_values) if cape_values else None
    cin_max = max(cin_values) if cin_values else None
    cin_label, cin_strength = _cin_label(cin_max)
    bullets = [
        f"CAPE: {_fmt_jkg(cape_max)} → {_cape_effect(cape_max)}",
        f"CIN: {cin_label} → {_cin_effect(cin_strength)}",
        f"Rüzgâr: {_fmt_wind(wind_direction, wind_speed)} → {_wind_effect(wind_direction, wind_speed)}",
    ]
    summary = f"CAPE {_fmt_jkg(cape_max)}, CIN {cin_label.lower()}, rüzgâr {_fmt_wind(wind_direction, wind_speed).lower()}"
    return ForecastAdjustment(
        name="ai_effect_analysis",
        value_c=0.0,
        summary=summary,
        inputs={
            "cape_max_jkg": cape_max,
            "cin_max_jkg": cin_max,
            "cin_strength": cin_strength,
            "wind_direction_deg": wind_direction,
            "wind_speed_kt": wind_speed,
            "bullets": bullets,
        },
    )


def _surface_wind(
    metar: METARNormalized | None,
    points: list[ModelHourlyPoint],
) -> tuple[float | None, float | None]:
    if metar is not None and metar.wind_direction_deg is not None:
        return float(metar.wind_direction_deg), metar.wind_speed_kt
    directions = [point.wind_direction_10m_deg for point in points if point.wind_direction_10m_deg is not None]
    speeds = [point.wind_speed_10m_kt for point in points if point.wind_speed_10m_kt is not None]
    direction = _circular_mean(directions) if directions else None
    speed = mean(speeds) if speeds else None
    return direction, speed


def _cape_effect(cape_jkg: float | None) -> str:
    if cape_jkg is None:
        return "Model CAPE verisi yok; lokal konveksiyon etkisi sayısallaştırılamadı."
    if cape_jkg < 100:
        return "Konvektif enerji çok düşük; ani bulutlanma baskısı zayıf."
    if cape_jkg < 500:
        return "Zayıf konveksiyon sinyali var; sıcaklık tavanına etkisi sınırlı."
    if cape_jkg < 1000:
        return "Öğleden sonra lokal konveksiyon riski var. Ani bulutlanma sıcaklığı baskılayabilir."
    if cape_jkg < 2000:
        return "Konvektif enerji belirgin; sağanak/fırtına gelişirse maksimum sıcaklık aşağı çekilebilir."
    return "Konvektif enerji yüksek; fırtına ve ani bulutlanma sıcaklık tahmininde ana risk."


def _cin_label(cin_jkg: float | None) -> tuple[str, str]:
    if cin_jkg is None:
        return "veri yok", "unknown"
    if cin_jkg >= 100:
        return "Güçlü", "strong"
    if cin_jkg >= 50:
        return "Orta", "moderate"
    if cin_jkg >= 10:
        return "Zayıf", "weak"
    return "Yok/zayıf", "minimal"


def _cin_effect(strength: str) -> str:
    if strength == "unknown":
        return "Model CIN verisi yok; CAPE’nin kullanılabilirliği belirsiz."
    if strength == "strong":
        return "Atmosfer şu an patlamayı baskılıyor. Fırtına oluşumu zor."
    if strength == "moderate":
        return "Kapak orta kuvvette; tetik gelirse gecikmeli ve lokal gelişim mümkün."
    if strength == "weak":
        return "Kapak zayıf; yeterli ısınma veya yakınsama olursa CAPE daha kolay kullanılabilir."
    return "Kapak belirgin değil; konvektif enerji varsa gelişimin önündeki engel sınırlı."


def _wind_effect(direction_deg: float | None, speed_kt: float | None) -> str:
    if direction_deg is None or speed_kt is None:
        return "Rüzgâr verisi yok; adveksiyon etkisi ayrı hesapta sınırlı kalıyor."
    label = _direction_label(direction_deg).capitalize()
    intensity = "hafif" if speed_kt < 15 else "belirgin" if speed_kt < 25 else "kuvvetli"
    normalized = direction_deg % 360
    if normalized <= 80 or normalized >= 320:
        return f"{label} akış {intensity} serinletici etki yaratıyor."
    if 160 <= normalized <= 250:
        return f"{label} akış {intensity} ısıtıcı etki yaratıyor."
    return f"{label} akış sıcaklık üzerinde sınırlı net etki bırakıyor."


def _direction_label(direction_deg: float) -> str:
    normalized = direction_deg % 360
    if normalized == 0:
        return "kuzeyli"
    if normalized == 90:
        return "doğulu"
    if normalized == 180:
        return "güneyli"
    if normalized == 270:
        return "batılı"
    if 0 < normalized < 90:
        return "kuzeydoğulu"
    if 90 < normalized < 180:
        return "güneydoğulu"
    if 180 < normalized < 270:
        return "güneybatılı"
    return "kuzeybatılı"


def _fmt_jkg(value: float | None) -> str:
    if value is None:
        return "veri yok"
    return f"{value:.0f} J/kg"


def _fmt_wind(direction_deg: float | None, speed_kt: float | None) -> str:
    if direction_deg is None or speed_kt is None:
        return "veri yok"
    return f"{direction_deg % 360:03.0f}° / {speed_kt:.0f} KT"


def _cin_magnitude(value: float | None) -> float | None:
    if value is None or value == -1:
        return None
    return abs(value)


def _circular_mean(values: list[float]) -> float:
    sin_sum = sum(math.sin(math.radians(value)) for value in values)
    cos_sum = sum(math.cos(math.radians(value)) for value in values)
    angle = math.degrees(math.atan2(sin_sum, cos_sum))
    return angle + 360 if angle < 0 else angle
