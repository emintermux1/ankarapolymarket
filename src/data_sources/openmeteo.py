from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import (
    EnsembleForecast,
    ModelBundle,
    ModelForecast,
    ModelHourlyPoint,
    SourceHealth,
    SourceState,
)


OPENMETEO_VARIABLES = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "precipitation",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "wind_speed_10m",
    "wind_direction_10m",
    "pressure_msl",
    "surface_pressure",
    "shortwave_radiation",
    "cape",
    "temperature_925hPa",
    "relative_humidity_700hPa",
    "temperature_850hPa",
    "geopotential_height_500hPa",
    "wind_speed_250hPa",
    "wind_direction_250hPa",
    "wind_speed_850hPa",
    "wind_direction_850hPa",
    "soil_temperature_0cm",
    "soil_moisture_0_to_1cm",
]

FIELD_MAP = {
    "temperature_2m": "temperature_2m_c",
    "relative_humidity_2m": "relative_humidity_pct",
    "dew_point_2m": "dew_point_2m_c",
    "precipitation": "precipitation_mm",
    "cloud_cover": "cloud_cover_pct",
    "cloud_cover_low": "cloud_cover_low_pct",
    "cloud_cover_mid": "cloud_cover_mid_pct",
    "cloud_cover_high": "cloud_cover_high_pct",
    "wind_speed_10m": "wind_speed_10m_kt",
    "wind_direction_10m": "wind_direction_10m_deg",
    "pressure_msl": "pressure_msl_hpa",
    "surface_pressure": "surface_pressure_hpa",
    "shortwave_radiation": "shortwave_radiation_wm2",
    "cape": "cape_jkg",
    "temperature_925hPa": "temperature_925hpa_c",
    "relative_humidity_700hPa": "relative_humidity_700hpa_pct",
    "temperature_850hPa": "temperature_850hpa_c",
    "geopotential_height_500hPa": "geopotential_height_500hpa_m",
    "wind_speed_250hPa": "wind_speed_250hpa_kt",
    "wind_direction_250hPa": "wind_direction_250hpa_deg",
    "wind_speed_850hPa": "wind_speed_850hpa_kt",
    "wind_direction_850hPa": "wind_direction_850hpa_deg",
    "soil_temperature_0cm": "soil_temperature_0cm_c",
    "soil_moisture_0_to_1cm": "soil_moisture_0_to_1cm_m3m3",
}

ENSEMBLE_ALIASES = {
    "ecmwf_ifs025": ["ecmwf_ifs025", "ecmwf"],
    "ecmwf_ifs04": ["ecmwf_ifs04"],
    "ecmwf_aifs025": ["ecmwf_aifs025", "aifs"],
    "gfs_seamless": ["gfs", "gefs", "ncep_gefs"],
    "icon_seamless": ["icon", "dwd_icon"],
    "icon_eu": ["icon_eu"],
    "icon_global": ["icon_global"],
}


class OpenMeteoAdapter(HttpSource):
    source_name = "Open-Meteo"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.forecast_url = "https://api.open-meteo.com/v1/forecast"
        self.ensemble_url = "https://ensemble-api.open-meteo.com/v1/ensemble"

    async def get_forecast(self, target_date: date) -> ModelBundle:
        models = self.settings.openmeteo_models
        payload = await self._request_json(
            self.forecast_url,
            params={
                "latitude": self.settings.ltac_latitude,
                "longitude": self.settings.ltac_longitude,
                "hourly": ",".join(OPENMETEO_VARIABLES),
                "models": ",".join(models),
                "timezone": self.settings.report_timezone,
                "forecast_days": 16,
                "wind_speed_unit": "kn",
                "cell_selection": "land",
                "bias_correction": str(self.settings.openmeteo_bias_correction).lower(),
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "Forecast payload is not an object")

        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        forecasts = [
            self._parse_model_forecast(model, target_date, hourly, times)
            for model in models
        ]
        return ModelBundle(
            fetch_timestamp=datetime.now(timezone.utc),
            target_date=target_date,
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),
            elevation_m=payload.get("elevation"),
            forecasts=forecasts,
            raw_json=payload,
        )

    async def get_ensemble(self, target_date: date) -> list[EnsembleForecast]:
        payload = await self._request_json(
            self.ensemble_url,
            params={
                "latitude": self.settings.ltac_latitude,
                "longitude": self.settings.ltac_longitude,
                "hourly": "temperature_2m",
                "models": ",".join(self.settings.openmeteo_ensemble_models),
                "timezone": self.settings.report_timezone,
                "forecast_days": 16,
            },
        )
        if not isinstance(payload, dict):
            raise SourceError(self.source_name, "Ensemble payload is not an object")
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        results: list[EnsembleForecast] = []
        for model in self.settings.openmeteo_ensemble_models:
            member_tmax = []
            aliases = ENSEMBLE_ALIASES.get(model, [model])
            matching_keys = [
                key
                for key in hourly
                if key.startswith("temperature_2m_member")
                and any(alias in key for alias in aliases)
            ]
            if not matching_keys and len(self.settings.openmeteo_ensemble_models) == 1:
                matching_keys = [key for key in hourly if key.startswith("temperature_2m_member")]
            for key in matching_keys:
                vals = [
                    value
                    for ts, value in zip(times, hourly.get(key) or [])
                    if str(ts).startswith(target_date.isoformat()) and value is not None
                ]
                if vals:
                    member_tmax.append(float(max(vals)))
            results.append(EnsembleForecast(model=model, target_date=target_date, member_tmax_c=member_tmax))
        return results

    async def get_bundle_with_ensemble(self, target_date: date) -> ModelBundle:
        bundle = await self.get_forecast(target_date)
        try:
            bundle.ensembles = await self.get_ensemble(target_date)
        except Exception as exc:
            self.logger.warning("ensemble fetch failed: %s", exc)
        return bundle

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            await self.get_forecast(datetime.now(ZoneInfo(self.settings.report_timezone)).date())
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))

    def _parse_model_forecast(
        self,
        model: str,
        target_date: date,
        hourly: dict[str, Any],
        times: list[str],
    ) -> ModelForecast:
        key_map = {variable: self._find_key(hourly, variable, model) for variable in OPENMETEO_VARIABLES}
        points: list[ModelHourlyPoint] = []
        tz = ZoneInfo(self.settings.report_timezone)
        for idx, ts in enumerate(times):
            if not str(ts).startswith(target_date.isoformat()):
                continue
            values: dict[str, Any] = {"time": datetime.fromisoformat(str(ts)).replace(tzinfo=tz)}
            has_any = False
            for variable, key in key_map.items():
                if key is None:
                    continue
                series = hourly.get(key) or []
                value = series[idx] if idx < len(series) else None
                if value is not None:
                    has_any = True
                values[FIELD_MAP[variable]] = value
            if has_any:
                points.append(ModelHourlyPoint(**values))

        temperatures = [point.temperature_2m_c for point in points if point.temperature_2m_c is not None]
        if not temperatures:
            return ModelForecast(
                model=model,
                available=False,
                target_date=target_date,
                raw_model_key_map={k: v for k, v in key_map.items() if v},
                unavailable_reason="temperature_2m unavailable/null for target date",
            )
        report_hour_values = [
            point.temperature_2m_c
            for point in points
            if point.time.hour == 9 and point.temperature_2m_c is not None
        ]
        return ModelForecast(
            model=model,
            available=True,
            target_date=target_date,
            hourly=points,
            tmax_c=float(max(temperatures)),
            expected_temp_at_report_hour_c=float(mean(report_hour_values)) if report_hour_values else None,
            raw_model_key_map={k: v for k, v in key_map.items() if v},
        )

    @staticmethod
    def _find_key(hourly: dict[str, Any], variable: str, model: str) -> str | None:
        suffixed = f"{variable}_{model}"
        if suffixed in hourly:
            return suffixed
        if variable in hourly:
            return variable
        for key in hourly:
            if key.startswith(f"{variable}_") and key.endswith(model):
                return key
        return None
