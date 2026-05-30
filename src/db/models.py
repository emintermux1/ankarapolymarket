from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("station", "observation_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetch_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    station: Mapped[str] = mapped_column(String(16), nullable=False, default="LTAC")
    temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    dew_point_c: Mapped[float] = mapped_column(Float, nullable=False)
    relative_humidity: Mapped[int | None] = mapped_column(Integer)
    wind_direction_deg: Mapped[int | None] = mapped_column(Integer)
    wind_speed_kt: Mapped[float] = mapped_column(Float, nullable=False)
    wind_gust_kt: Mapped[float | None] = mapped_column(Float)
    pressure_hpa: Mapped[float | None] = mapped_column(Float)
    visibility_m: Mapped[int | None] = mapped_column(Integer)
    cloud_layers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    raw_metar: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class TAF(Base):
    __tablename__ = "tafs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetch_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issue_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), unique=True, nullable=False)
    station: Mapped[str] = mapped_column(String(16), nullable=False, default="LTAC")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_taf: Mapped[str] = mapped_column(Text, nullable=False)
    periods: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ModelSnapshot(Base):
    __tablename__ = "model_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetch_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    available: Mapped[bool] = mapped_column(nullable=False)
    tmax_c: Mapped[float | None] = mapped_column(Float)
    expected_temp_at_report_hour_c: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ForecastRun(Base):
    __tablename__ = "forecast_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    final_tmax_prediction: Mapped[float | None] = mapped_column(Float)
    weighted_model_tmax: Mapped[float | None] = mapped_column(Float)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    model_spread_c: Mapped[float | None] = mapped_column(Float)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    formula_adjustments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class MarketSnapshotRecord(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fetch_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False)
    closed: Mapped[bool] = mapped_column(nullable=False)
    valid_for_target: Mapped[bool] = mapped_column(nullable=False)
    liquidity: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class DailyPrediction(Base):
    __tablename__ = "daily_predictions"
    __table_args__ = (UniqueConstraint("prediction_date", "report_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_label: Mapped[str] = mapped_column(String(32), nullable=False, default="09:00")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    final_tmax_prediction: Mapped[float | None] = mapped_column(Float)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    formula_adjustments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    bracket_probabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ActualResultRecord(Base):
    __tablename__ = "actual_results"
    __table_args__ = (UniqueConstraint("target_date", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tmax_c: Mapped[float | None] = mapped_column(Float)
    rounded_tmax_c: Mapped[int | None] = mapped_column(Integer)
    unavailable_reason: Mapped[str | None] = mapped_column(Text)
    manual_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class SourceStatus(Base):
    __tablename__ = "source_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    message: Mapped[str | None] = mapped_column(Text)


class TelegramDelivery(Base):
    __tablename__ = "telegram_deliveries"
    __table_args__ = (UniqueConstraint("delivery_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    delivery_key: Mapped[str] = mapped_column(String(160), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class BacktestScore(Base):
    __tablename__ = "backtest_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    score_date: Mapped[date] = mapped_column(Date, nullable=False)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    mae: Mapped[float | None] = mapped_column(Float)
    bias: Mapped[float | None] = mapped_column(Float)
    hit_rate: Mapped[float | None] = mapped_column(Float)
    bracket_accuracy: Mapped[float | None] = mapped_column(Float)
    calibration_score: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ModelWeight(Base):
    __tablename__ = "model_weights"
    __table_args__ = (UniqueConstraint("model", "calculated_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    mae_7: Mapped[float | None] = mapped_column(Float)
    mae_14: Mapped[float | None] = mapped_column(Float)
    mae_30: Mapped[float | None] = mapped_column(Float)
    bias_7: Mapped[float | None] = mapped_column(Float)
    bias_14: Mapped[float | None] = mapped_column(Float)
    bias_30: Mapped[float | None] = mapped_column(Float)


class AnalogDay(Base):
    __tablename__ = "analog_days"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    analog_date: Mapped[date] = mapped_column(Date, nullable=False)
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    setup_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    actual_tmax_c: Mapped[float | None] = mapped_column(Float)
