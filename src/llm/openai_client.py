from __future__ import annotations

import logging
import re

import httpx

from src.config import Settings

logger = logging.getLogger(__name__)


class OpenAIReportClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.url = "https://api.openai.com/v1/chat/completions"

    @property
    def enabled(self) -> bool:
        return bool(self.settings.openai_api_key and self.settings.llm_report_summary)

    async def summarize(self, report_text: str) -> str | None:
        if not self.enabled:
            return None
        prompt = (
            "Aşağıdaki Ankara LTAC maksimum sıcaklık bot raporunu Türkçe, 3 kısa maddeyle yorumla. "
            "Belirsizliği ve en kritik hava/market noktasını belirt. Yatırım tavsiyesi verme, token/key/secret yazma.\n\n"
            f"{report_text[:6000]}"
        )
        try:
            async with httpx.AsyncClient(timeout=min(self.settings.http_timeout_seconds, 20.0)) as client:
                response = await client.post(
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self.settings.openai_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.settings.openai_model,
                        "messages": [
                            {"role": "system", "content": "You are a cautious aviation weather forecasting assistant."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 220,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning("OpenAI report summary failed: %s", _redact(str(exc)))
            return None
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not choices:
            return None
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        return str(content).strip() if content else None


def _redact(message: str) -> str:
    return re.sub(r"(Authorization: Bearer\s+)[^\s]+", r"\1<redacted>", message)
