from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import create_engine, desc, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import Settings
from src.data_sources.schemas import (
    ActualResult,
    ForecastAnalysis,
    MarketSnapshot,
    METARNormalized,
    ModelBundle,
    SourceHealth,
    TAFNormalized,
    round_market_temperature_c,
)
from src.db.models import (
    ActualResultRecord,
    BacktestScore,
    Base,
    DailyPrediction,
    ForecastRun,
    MarketSnapshotRecord,
    ModelSnapshot,
    ModelWeight,
    NotificationState,
    Observation,
    SourceStatus,
    TAF,
)


class Repository:
    def __init__(self, engine: Engine, session_factory: sessionmaker[Session]) -> None:
        self.engine = engine
        self.session_factory = session_factory

    def init_db(self) -> None:
        Base.metadata.create_all(self.engine)

    def save_observation(self, metar: METARNormalized) -> None:
        with self.session_factory() as session:
            exists = session.scalar(select(Observation).where(Observation.observation_time == metar.observation_time))
            if exists:
                return
            session.add(
                Observation(
                    fetch_timestamp=metar.fetch_timestamp,
                    observation_time=metar.observation_time,
                    station=metar.station,
                    temperature_c=metar.temperature_c,
                    dew_point_c=metar.dew_point_c,
                    relative_humidity=metar.relative_humidity,
                    wind_direction_deg=metar.wind_direction_deg,
                    wind_speed_kt=metar.wind_speed_kt,
                    wind_gust_kt=metar.wind_gust_kt,
                    pressure_hpa=metar.pressure_hpa,
                    visibility_m=metar.visibility_m,
                    cloud_layers=metar.cloud_layers,
                    raw_metar=metar.raw_text,
                    raw_json=metar.raw_json,
                )
            )
            session.commit()

    def save_taf(self, taf: TAFNormalized) -> None:
        with self.session_factory() as session:
            exists = session.scalar(select(TAF).where(TAF.issue_time == taf.issue_time))
            if exists:
                return
            session.add(
                TAF(
                    fetch_timestamp=taf.fetch_timestamp,
                    issue_time=taf.issue_time,
                    station=taf.station,
                    valid_from=taf.valid_from,
                    valid_to=taf.valid_to,
                    raw_taf=taf.raw_text,
                    periods=[period.model_dump(mode="json") for period in taf.periods],
                    raw_json=taf.raw_json,
                )
            )
            session.commit()

    def save_model_bundle(self, bundle: ModelBundle) -> None:
        with self.session_factory() as session:
            for forecast in bundle.forecasts:
                session.add(
                    ModelSnapshot(
                        fetch_timestamp=bundle.fetch_timestamp,
                        target_date=bundle.target_date,
                        source=bundle.source,
                        model=forecast.model,
                        available=forecast.available,
                        tmax_c=forecast.tmax_c,
                        expected_temp_at_report_hour_c=forecast.expected_temp_at_report_hour_c,
                        payload=forecast.model_dump(mode="json"),
                    )
                )
            session.commit()

    def save_market_snapshot(self, snapshot: MarketSnapshot | None) -> None:
        if snapshot is None:
            return
        with self.session_factory() as session:
            session.add(
                MarketSnapshotRecord(
                    fetch_timestamp=snapshot.fetch_timestamp,
                    event_id=snapshot.event_id,
                    target_date=snapshot.target_date,
                    title=snapshot.title,
                    link=snapshot.link,
                    active=snapshot.active,
                    closed=snapshot.closed,
                    valid_for_target=snapshot.valid_for_target,
                    liquidity=snapshot.liquidity,
                    volume=snapshot.volume,
                    payload=snapshot.model_dump(mode="json"),
                )
            )
            session.commit()

    def save_forecast_analysis(
        self,
        analysis: ForecastAnalysis,
        report_label: str = "09:00",
    ) -> None:
        payload = analysis.model_dump(mode="json")
        with self.session_factory() as session:
            session.add(
                ForecastRun(
                    target_date=analysis.target_date,
                    generated_at=analysis.generated_at,
                    final_tmax_prediction=analysis.final_tmax_c,
                    weighted_model_tmax=analysis.weighted_model_tmax_c,
                    confidence_score=analysis.confidence_score,
                    model_spread_c=analysis.model_spread_c,
                    verdict=analysis.verdict,
                    formula_adjustments={item.name: item.model_dump(mode="json") for item in analysis.adjustments},
                    payload=payload,
                )
            )
            existing = session.scalar(
                select(DailyPrediction).where(
                    DailyPrediction.prediction_date == analysis.target_date,
                    DailyPrediction.report_label == report_label,
                )
            )
            if existing:
                existing.generated_at = analysis.generated_at
                existing.final_tmax_prediction = analysis.final_tmax_c
                existing.confidence_score = analysis.confidence_score
                existing.formula_adjustments = {item.name: item.model_dump(mode="json") for item in analysis.adjustments}
                existing.bracket_probabilities = analysis.fair_probabilities
                existing.payload = payload
            else:
                session.add(
                    DailyPrediction(
                        prediction_date=analysis.target_date,
                        report_label=report_label,
                        generated_at=analysis.generated_at,
                        final_tmax_prediction=analysis.final_tmax_c,
                        confidence_score=analysis.confidence_score,
                        formula_adjustments={item.name: item.model_dump(mode="json") for item in analysis.adjustments},
                        bracket_probabilities=analysis.fair_probabilities,
                        payload=payload,
                    )
                )
            session.commit()

    def save_actual_result(self, result: ActualResult) -> None:
        with self.session_factory() as session:
            existing = session.scalar(
                select(ActualResultRecord).where(
                    ActualResultRecord.target_date == result.target_date,
                    ActualResultRecord.source == result.source,
                )
            )
            if existing:
                existing.fetched_at = result.fetched_at
                existing.tmax_c = result.tmax_c
                existing.rounded_tmax_c = result.rounded_tmax_c
                existing.unavailable_reason = result.unavailable_reason
                existing.manual_required = result.manual_required
                existing.raw_payload = result.raw_payload
            else:
                session.add(
                    ActualResultRecord(
                        target_date=result.target_date,
                        source=result.source,
                        fetched_at=result.fetched_at,
                        tmax_c=result.tmax_c,
                        rounded_tmax_c=result.rounded_tmax_c,
                        unavailable_reason=result.unavailable_reason,
                        manual_required=result.manual_required,
                        raw_payload=result.raw_payload,
                    )
                )
            session.commit()

    def save_source_health(self, health: SourceHealth) -> None:
        with self.session_factory() as session:
            session.add(
                SourceStatus(
                    source=health.source,
                    state=health.state.value,
                    checked_at=health.checked_at,
                    latency_ms=health.latency_ms,
                    message=health.message,
                )
            )
            session.commit()

    def latest_source_health(self) -> list[SourceHealth]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(SourceStatus)
                .order_by(SourceStatus.source, desc(SourceStatus.checked_at))
            ).all()
        seen: set[str] = set()
        result = []
        for row in rows:
            if row.source in seen:
                continue
            seen.add(row.source)
            result.append(
                SourceHealth(
                    source=row.source,
                    state=row.state,
                    checked_at=row.checked_at,
                    latency_ms=row.latency_ms,
                    message=row.message,
                )
            )
        return result

    def notification_state(self, key: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.get(NotificationState, key)
            return row.payload if row else None

    def save_notification_state(self, key: str, payload: dict[str, Any]) -> None:
        with self.session_factory() as session:
            row = session.get(NotificationState, key)
            now = datetime.now(timezone.utc)
            if row:
                row.updated_at = now
                row.payload = payload
            else:
                session.add(NotificationState(key=key, updated_at=now, payload=payload))
            session.commit()

    def latest_model_weights(self, models: list[str]) -> dict[str, dict[str, float | None]]:
        weights: dict[str, dict[str, float | None]] = {}
        with self.session_factory() as session:
            for model in models:
                row = session.scalar(
                    select(ModelWeight)
                    .where(ModelWeight.model == model)
                    .order_by(desc(ModelWeight.calculated_at))
                    .limit(1)
                )
                if row:
                    weights[model] = {
                        "weight": row.weight,
                        "mae_7": row.mae_7,
                        "mae_14": row.mae_14,
                        "mae_30": row.mae_30,
                        "bias_7": row.bias_7,
                        "bias_14": row.bias_14,
                        "bias_30": row.bias_30,
                    }
        return weights

    def latest_model_tmax_by_target(self, target_date: date) -> dict[str, float | None]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(ModelSnapshot)
                .where(ModelSnapshot.target_date == target_date)
                .order_by(ModelSnapshot.model, desc(ModelSnapshot.fetch_timestamp), desc(ModelSnapshot.id))
            ).all()
        latest: dict[str, float | None] = {}
        for row in rows:
            if row.model in latest:
                continue
            latest[row.model] = row.tmax_c if row.available else None
        return latest

    def latest_backtest_summary(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(select(BacktestScore).order_by(desc(BacktestScore.score_date)).limit(20)).all()
        return [
            {
                "score_date": row.score_date.isoformat(),
                "window_days": row.window_days,
                "model": row.model,
                "mae": row.mae,
                "bias": row.bias,
                "hit_rate": row.hit_rate,
                "bracket_accuracy": row.bracket_accuracy,
                "calibration_score": row.calibration_score,
            }
            for row in rows
        ]

    def latest_prediction(self, target_date: date) -> dict[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(DailyPrediction)
                .where(DailyPrediction.prediction_date == target_date)
                .order_by(desc(DailyPrediction.generated_at))
                .limit(1)
            )
        return row.payload if row else None


def create_repository(settings: Settings) -> Repository:
    connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
    engine = create_engine(settings.database_url, future=True, pool_pre_ping=True, connect_args=connect_args)
    session_factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    repo = Repository(engine, session_factory)
    repo.init_db()
    return repo


def manual_actual_result(target_date: date, tmax_c: float) -> ActualResult:
    return ActualResult(
        target_date=target_date,
        source="manual_wunderground_final",
        fetched_at=datetime.now(timezone.utc),
        tmax_c=tmax_c,
        rounded_tmax_c=round_market_temperature_c(tmax_c),
        raw_payload={"entered_manually": True},
    )
