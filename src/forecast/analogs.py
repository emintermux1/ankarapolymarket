from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class AnalogCandidate:
    analog_date: date
    similarity_score: float
    actual_tmax_c: float | None
    reason: str


def rank_analog_days(candidates: list[AnalogCandidate], limit: int = 5) -> list[AnalogCandidate]:
    return sorted(candidates, key=lambda item: item.similarity_score, reverse=True)[:limit]

