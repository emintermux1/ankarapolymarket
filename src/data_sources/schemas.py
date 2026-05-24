from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from math import exp
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SourceState(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    UNAVAILABLE = "unavailable"


class SourceHealth(BaseModel):
    source: str
    state: SourceState
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    latency_ms: float | None = None
    message: str | None = None


class METARNormalized(BaseModel):
    source: str = "AviationWeather"
    station: str = "LTAC"
    fetch_timestamp: datetime
    observation_time: datetime
    temperature_c: float = Field(..., ge=-40.0, le=55.0)
    dew_point_c: float = Field(..., ge=-60.0, le=45.0)
    relative_humidity: int | None = Field(default=None, ge=0, le=100)
    wind_direction_deg: int | None = Field(default=None, ge=0, le=360)
    wind_speed_kt: float = Field(..., ge=0.0)
    wind_gust_kt: float | None = Field(default=None, ge=0.0)
    pressure_hpa: float | None = Field(default=None, ge=850.0, le=1100.0)
    visibility_m: int | None = None
    cloud_layers: list[dict[str, Any]] = Field(default_factory=list)
    raw_text: str
    raw_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("dew_point_c")
    @classmethod
    def validate_dew_point(cls, value: float, info: Any) -> float:
        temperature = info.data.get("temperature_c")
        if temperature is not None and value > temperature + 0.1:
            raise ValueError("Dew point cannot exceed observed air temperature.")
        return value

    @property
    def age_minutes(self) -> float:
        now = datetime.now(timezone.utc)
        observed = self.observation_time.astimezone(timezone.utc)
        return max(0.0, (now - observed).total_seconds() / 60.0)

    @property
    def is_stale(self) -> bool:
        return self.age_minutes > 90.0


class TAFForecastPeriod(BaseModel):
    time_from: datetime
    time_to: datetime
    change: str | None = None
    probability: int | None = None
    wind_direction_deg: int | None = None
    wind_speed_kt: float | None = None
    wind_gust_kt: float | None = None
    visibility_m: int | None = None
    weather: str | None = None
    clouds: list[dict[str, Any]] = Field(default_factory=list)


class TAFNormalized(BaseModel):
    source: str = "AviationWeather"
    station: str = "LTAC"
    fetch_timestamp: datetime
    issue_time: datetime
    valid_from: datetime
    valid_to: datetime
    raw_text: str
    periods: list[TAFForecastPeriod] = Field(default_factory=list)
    raw_json: dict[str, Any] = Field(default_factory=dict)

    @property
    def rain_or_storm_risk(self) -> bool:
        tokens = ("RA", "SHRA", "TS", "TSRA", "CB")
        for period in self.periods:
            if period.weather and any(token in period.weather for token in tokens):
                return True
            if any(cloud.get("type") == "CB" for cloud in period.clouds):
                return True
        return False


class ModelHourlyPoint(BaseModel):
    time: datetime
    temperature_2m_c: float | None = None
    relative_humidity_pct: float | None = None
    dew_point_2m_c: float | None = None
    precipitation_mm: float | None = None
    cloud_cover_pct: float | None = None
    cloud_cover_low_pct: float | None = None
    cloud_cover_mid_pct: float | None = None
    cloud_cover_high_pct: float | None = None
    wind_speed_10m_kt: float | None = None
    wind_direction_10m_deg: float | None = None
    pressure_msl_hpa: float | None = None
    surface_pressure_hpa: float | None = None
    shortwave_radiation_wm2: float | None = None
    cape_jkg: float | None = None
    convective_inhibition_jkg: float | None = None
    temperature_850hpa_c: float | None = None
    geopotential_height_500hpa_m: float | None = None
    wind_speed_850hpa_kt: float | None = None
    wind_direction_850hpa_deg: float | None = None
    cloud_base_m: float | None = None
    cloud_ceiling_m: float | None = None


class ModelForecast(BaseModel):
    model: str
    available: bool
    target_date: date
    hourly: list[ModelHourlyPoint] = Field(default_factory=list)
    tmax_c: float | None = None
    expected_temp_at_report_hour_c: float | None = None
    raw_model_key_map: dict[str, str] = Field(default_factory=dict)
    unavailable_reason: str | None = None

    @property
    def midday_points(self) -> list[ModelHourlyPoint]:
        return [point for point in self.hourly if 10 <= point.time.hour <= 14]


class EnsembleForecast(BaseModel):
    model: str
    target_date: date
    member_tmax_c: list[float] = Field(default_factory=list)


class ModelBundle(BaseModel):
    source: str = "Open-Meteo"
    fetch_timestamp: datetime
    target_date: date
    latitude: float | None = None
    longitude: float | None = None
    elevation_m: float | None = None
    forecasts: list[ModelForecast] = Field(default_factory=list)
    ensembles: list[EnsembleForecast] = Field(default_factory=list)
    raw_json: dict[str, Any] = Field(default_factory=dict)

    @property
    def available_forecasts(self) -> list[ModelForecast]:
        return [forecast for forecast in self.forecasts if forecast.available and forecast.tmax_c is not None]


class ForumPost(BaseModel):
    source: str = "HavaForum"
    post_id: str
    url: str
    author: str | None = None
    published_at: datetime
    text: str
    matches_target_context: bool = True


class ForumAnalysis(BaseModel):
    source: str = "HavaForum"
    fetch_timestamp: datetime
    target_date: date
    thread_url: str
    posts: list[ForumPost] = Field(default_factory=list)
    same_day_post_count: int = 0
    previous_day_tomorrow_post_count: int = 0
    latest_post_at: datetime | None = None
    locations: list[str] = Field(default_factory=list)
    signals: dict[str, int] = Field(default_factory=dict)
    summary: str = "Forum verisi yok."
    unavailable_reason: str | None = None

    @property
    def post_count(self) -> int:
        return len(self.posts)


class OrderBookLevel(BaseModel):
    price: float
    size: float


class MarketOutcome(BaseModel):
    question: str
    bracket: str
    condition_id: str | None = None
    yes_token_id: str | None = None
    no_token_id: str | None = None
    yes_price: float | None = None
    no_price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    liquidity: float | None = None
    volume: float | None = None
    recent_trades: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def implied_probability(self) -> float | None:
        if self.yes_price is not None:
            return self.yes_price
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2.0
        return None


class MarketSnapshot(BaseModel):
    source: str = "Polymarket"
    fetch_timestamp: datetime
    event_id: str
    title: str
    slug: str
    target_date: date | None = None
    active: bool
    closed: bool
    valid_for_target: bool
    validation_message: str | None = None
    link: str
    resolution_source: str | None = None
    liquidity: float | None = None
    volume: float | None = None
    outcomes: list[MarketOutcome] = Field(default_factory=list)
    raw_json: dict[str, Any] = Field(default_factory=dict)


class ForecastAdjustment(BaseModel):
    name: str
    value_c: float
    summary: str
    inputs: dict[str, Any] = Field(default_factory=dict)


class ForecastAnalysis(BaseModel):
    target_date: date
    generated_at: datetime
    report_timezone: str
    weighted_model_tmax_c: float | None
    final_tmax_c: float | None
    main_range_low_c: float | None
    main_range_high_c: float | None
    model_spread_c: float | None
    ensemble_sigma_c: float | None = None
    probability_sigma_c: float | None = None
    confidence_score: int
    confidence_factors: dict[str, Any]
    verdict: str
    adjustments: list[ForecastAdjustment] = Field(default_factory=list)
    model_weights: dict[str, float] = Field(default_factory=dict)
    model_bias_offsets: dict[str, float] = Field(default_factory=dict)
    fair_probabilities: dict[str, float] = Field(default_factory=dict)
    edge_summary: str = "Edge yok"
    rationale_bullets: list[str] = Field(default_factory=list)
    risks: dict[str, str] = Field(default_factory=dict)


class ActualResult(BaseModel):
    target_date: date
    source: str
    fetched_at: datetime
    tmax_c: float | None = None
    rounded_tmax_c: int | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    unavailable_reason: str | None = None
    manual_required: bool = False


def relative_humidity_from_temp_dewpoint(temp_c: float, dewpoint_c: float) -> int:
    numerator = exp((17.625 * dewpoint_c) / (243.04 + dewpoint_c))
    denominator = exp((17.625 * temp_c) / (243.04 + temp_c))
    return int(round(max(0.0, min(100.0, 100.0 * numerator / denominator))))


def round_market_temperature_c(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
