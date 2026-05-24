from __future__ import annotations

from src.data_sources.schemas import ForecastAdjustment, METARNormalized

LTAC_WESTERLY_ASPHALT_OFFSET_C = 0.4
WESTERLY_ASPHALT_MIN_DEG = 240
WESTERLY_ASPHALT_MAX_DEG = 300


def calculate_ltac_microclimate_adjustment(metar: METARNormalized | None) -> ForecastAdjustment:
    inputs = {
        "west_asphalt_offset_c": LTAC_WESTERLY_ASPHALT_OFFSET_C,
        "west_wind_sector_deg": [WESTERLY_ASPHALT_MIN_DEG, WESTERLY_ASPHALT_MAX_DEG],
    }
    if metar is None:
        return ForecastAdjustment(
            name="ltac_microclimate",
            value_c=0.0,
            summary="METAR verisi yok; Esenboğa pist/asfalt düzeltmesi kapalı",
            inputs={**inputs, "active": False},
        )

    direction = metar.wind_direction_deg
    inputs.update({"station": metar.station, "wind_direction_deg": direction})
    if direction is not None and is_ltac_westerly_asphalt_wind(direction):
        return ForecastAdjustment(
            name="ltac_microclimate",
            value_c=LTAC_WESTERLY_ASPHALT_OFFSET_C,
            summary="Esenboğa batı rüzgârı pist/asfalt ısısını METAR termometresine taşıyor",
            inputs={**inputs, "active": True},
        )

    if direction is None:
        summary = "METAR rüzgâr yönü VRB; Esenboğa pist/asfalt düzeltmesi kapalı"
    else:
        summary = f"METAR rüzgârı {direction}°; Esenboğa batı rüzgârı pist/asfalt düzeltmesi tetiklenmedi"
    return ForecastAdjustment(
        name="ltac_microclimate",
        value_c=0.0,
        summary=summary,
        inputs={**inputs, "active": False},
    )


def is_ltac_westerly_asphalt_wind(direction_deg: int | float) -> bool:
    return WESTERLY_ASPHALT_MIN_DEG <= direction_deg <= WESTERLY_ASPHALT_MAX_DEG
