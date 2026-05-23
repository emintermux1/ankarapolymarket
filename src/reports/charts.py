from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from src.config import Settings
from src.data_sources.schemas import ModelBundle, METARNormalized


class ChartRenderer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.chart_dir.mkdir(parents=True, exist_ok=True)

    def model_comparison(self, bundle: ModelBundle) -> Path:
        path = self.settings.chart_dir / f"model_comparison_{bundle.target_date.isoformat()}.png"
        fig, ax = plt.subplots(figsize=(9, 5))
        for forecast in bundle.available_forecasts:
            xs = [point.time for point in forecast.hourly if point.temperature_2m_c is not None]
            ys = [point.temperature_2m_c for point in forecast.hourly if point.temperature_2m_c is not None]
            ax.plot(xs, ys, marker="o", linewidth=1.8, label=forecast.model)
        ax.set_title(f"LTAC Model Sıcaklık Eğrisi - {bundle.target_date.isoformat()}")
        ax.set_ylabel("°C")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        return path

    def observed_vs_forecast(self, bundle: ModelBundle, metar: METARNormalized | None) -> Path:
        path = self.settings.chart_dir / f"observed_vs_forecast_{bundle.target_date.isoformat()}.png"
        fig, ax = plt.subplots(figsize=(9, 5))
        for forecast in bundle.available_forecasts:
            xs = [point.time for point in forecast.hourly if point.temperature_2m_c is not None]
            ys = [point.temperature_2m_c for point in forecast.hourly if point.temperature_2m_c is not None]
            ax.plot(xs, ys, alpha=0.65, label=forecast.model)
        if metar:
            ax.scatter([metar.observation_time], [metar.temperature_c], color="black", s=70, label="Son METAR", zorder=5)
        ax.set_title("LTAC Gözlem vs Model Patikası")
        ax.set_ylabel("°C")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        return path

    def confidence_chart(self, rows: list[dict]) -> Path:
        path = self.settings.chart_dir / f"confidence_{datetime.utcnow():%Y%m%d%H%M%S}.png"
        fig, ax = plt.subplots(figsize=(9, 4))
        if rows:
            xs = [row.get("score_date") for row in rows]
            ys = [row.get("calibration_score") or 0 for row in rows]
            ax.plot(xs, ys, marker="o")
        ax.set_title("Günlük Güven/Kalibrasyon")
        ax.set_ylim(0, 100)
        ax.grid(True, alpha=0.3)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        return path

