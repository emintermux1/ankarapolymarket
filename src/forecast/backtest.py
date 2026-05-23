from __future__ import annotations

from dataclasses import dataclass
from statistics import mean


@dataclass(frozen=True)
class ErrorPoint:
    model: str
    prediction: float
    actual: float

    @property
    def error(self) -> float:
        return self.prediction - self.actual

    @property
    def absolute_error(self) -> float:
        return abs(self.error)


def mae(points: list[ErrorPoint]) -> float | None:
    if not points:
        return None
    return mean(point.absolute_error for point in points)


def bias(points: list[ErrorPoint]) -> float | None:
    if not points:
        return None
    return mean(point.error for point in points)


def bracket_hit(prediction: float, actual: float, half_width: float = 0.5) -> bool:
    return prediction - half_width <= actual <= prediction + half_width

