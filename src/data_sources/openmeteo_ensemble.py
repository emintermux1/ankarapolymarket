from __future__ import annotations

from datetime import date

from src.config import Settings
from src.data_sources.openmeteo import OpenMeteoAdapter
from src.data_sources.schemas import EnsembleForecast


class OpenMeteoEnsembleAdapter(OpenMeteoAdapter):
    async def get_member_tmax(self, target_date: date) -> list[EnsembleForecast]:
        return await self.get_ensemble(target_date)

