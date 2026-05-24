from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from src.config import Settings

T = TypeVar("T")


class SourceError(RuntimeError):
    def __init__(self, source: str, message: str) -> None:
        message = redact_sensitive_url_values(message)
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message


def redact_sensitive_url_values(message: str) -> str:
    return re.sub(
        r"([?&](?:apikey|apiKey|key|token|api_key|access_token)=)[^&\s'\"]+",
        r"\1<redacted>",
        str(message),
        flags=re.IGNORECASE,
    )


class HttpSource:
    source_name = "http"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger(f"src.data_sources.{self.source_name}")

    async def _request_json(self, url: str, **kwargs: object) -> object:
        async def do_request() -> object:
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers={"User-Agent": "ankara-ltac-weather-bot/0.1"},
                follow_redirects=True,
            ) as client:
                response = await client.get(url, **kwargs)
                response.raise_for_status()
                return response.json()

        return await self._with_retries(do_request)

    async def _request_text(self, url: str, **kwargs: object) -> str:
        async def do_request() -> str:
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers={"User-Agent": "Mozilla/5.0 ankara-ltac-weather-bot/0.1"},
                follow_redirects=True,
            ) as client:
                response = await client.get(url, **kwargs)
                response.raise_for_status()
                return response.text

        return await self._with_retries(do_request)

    async def _request_bytes(self, url: str, **kwargs: object) -> bytes:
        async def do_request() -> bytes:
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers={"User-Agent": "ankara-ltac-weather-bot/0.1"},
                follow_redirects=True,
            ) as client:
                response = await client.get(url, **kwargs)
                response.raise_for_status()
                return response.content

        return await self._with_retries(do_request)

    async def _with_retries(self, fn: Callable[[], Awaitable[T]]) -> T:
        last_error: Exception | None = None
        for attempt in range(self.settings.http_retries + 1):
            try:
                return await fn()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt >= self.settings.http_retries:
                    break
                await asyncio.sleep(0.3 * (attempt + 1))
        raise SourceError(self.source_name, str(last_error))
