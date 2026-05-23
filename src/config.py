from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANKARA_TELEGRAM_BOT_TOKEN", "LTAC_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    )
    telegram_channel_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANKARA_TELEGRAM_CHANNEL_ID", "LTAC_TELEGRAM_CHANNEL_ID", "TELEGRAM_CHANNEL_ID"),
    )
    telegram_admin_ids: Annotated[list[int], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices("ANKARA_TELEGRAM_ADMIN_IDS", "LTAC_TELEGRAM_ADMIN_IDS", "TELEGRAM_ADMIN_IDS"),
    )
    telegram_allowed_chat_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "ANKARA_TELEGRAM_ALLOWED_CHAT_IDS",
            "LTAC_TELEGRAM_ALLOWED_CHAT_IDS",
            "TELEGRAM_ALLOWED_CHAT_IDS",
        ),
    )
    telegram_restrict_commands: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "ANKARA_TELEGRAM_RESTRICT_COMMANDS",
            "LTAC_TELEGRAM_RESTRICT_COMMANDS",
            "TELEGRAM_RESTRICT_COMMANDS",
        ),
    )

    database_url: str = Field(default="sqlite:///./data/ltac_weather_bot.db", alias="DATABASE_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    ltac_icao: str = Field(default="LTAC", alias="LTAC_ICAO")
    ltac_latitude: float = Field(default=40.1281, alias="LTAC_LATITUDE")
    ltac_longitude: float = Field(default=32.9951, alias="LTAC_LONGITUDE")
    ltac_elevation_m: int = Field(default=953, alias="LTAC_ELEVATION_M")
    report_timezone: str = Field(default="Europe/Istanbul", alias="REPORT_TIMEZONE")

    polymarket_event_slug: str = Field(
        default="highest-temperature-in-ankara-on-may-24-2026",
        alias="POLYMARKET_EVENT_SLUG",
    )
    polymarket_target_location_terms: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["ankara", "esenboğa", "esenboga", "ltac"],
        alias="POLYMARKET_TARGET_LOCATION_TERMS",
    )
    openmeteo_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["icon_eu", "ecmwf_ifs025", "icon_global", "gfs_seamless"],
        alias="OPENMETEO_MODELS",
    )
    openmeteo_ensemble_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["icon_eu", "ecmwf_ifs025", "icon_global", "gfs_seamless"],
        alias="OPENMETEO_ENSEMBLE_MODELS",
    )
    openmeteo_bias_correction: bool = Field(default=True, alias="OPENMETEO_BIAS_CORRECTION")

    http_timeout_seconds: float = Field(default=20.0, alias="HTTP_TIMEOUT_SECONDS")
    http_retries: int = Field(default=2, alias="HTTP_RETRIES")

    schedule_daily_report: str = Field(default="09:00", alias="SCHEDULE_DAILY_REPORT")
    schedule_midday_update: str = Field(default="12:00", alias="SCHEDULE_MIDDAY_UPDATE")
    schedule_risk_update: str = Field(default="15:00", alias="SCHEDULE_RISK_UPDATE")
    schedule_result_report: str = Field(default="21:00", alias="SCHEDULE_RESULT_REPORT")

    weathercom_api_key: str | None = Field(default=None, alias="WEATHERCOM_API_KEY")
    checkwx_api_key: str | None = Field(default=None, alias="CHECKWX_API_KEY")
    avwx_api_key: str | None = Field(default=None, alias="AVWX_API_KEY")
    visualcrossing_api_key: str | None = Field(default=None, alias="VISUALCROSSING_API_KEY")
    weatherapi_api_key: str | None = Field(default=None, alias="WEATHERAPI_API_KEY")
    tomorrow_api_key: str | None = Field(default=None, alias="TOMORROW_API_KEY")
    meteoblue_api_key: str | None = Field(default=None, alias="METEOBLUE_API_KEY")
    windy_api_key: str | None = Field(default=None, alias="WINDY_API_KEY")

    data_dir: Path = Path("data")
    chart_dir: Path = Path("data/charts")

    @field_validator(
        "telegram_admin_ids",
        "telegram_allowed_chat_ids",
        "polymarket_target_location_terms",
        "openmeteo_models",
        "openmeteo_ensemble_models",
        mode="before",
    )
    @classmethod
    def parse_csv_list(cls, value: Any) -> Any:
        if value is None or isinstance(value, list):
            return value
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            items = [part.strip() for part in value.split(",") if part.strip()]
            return items
        return value

    @field_validator("telegram_admin_ids", mode="after")
    @classmethod
    def parse_admin_ids(cls, value: list[Any]) -> list[int]:
        return [int(item) for item in value]

    @property
    def telegram_allowed_chat_keys(self) -> set[str]:
        keys = {str(item).strip().lower() for item in self.telegram_allowed_chat_ids if str(item).strip()}
        if self.telegram_channel_id:
            keys.add(str(self.telegram_channel_id).strip().lower())
        keys.update(str(item) for item in self.telegram_admin_ids)
        return keys

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


def setup_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.chart_dir.mkdir(parents=True, exist_ok=True)
    return settings
