from __future__ import annotations

from telegram import LinkPreviewOptions

DISABLE_LINK_PREVIEWS = LinkPreviewOptions(is_disabled=True)


def format_telegram_text(text: str) -> str:
    return "\n".join(_format_line(line) for line in text.splitlines())


def _format_line(line: str) -> str:
    if line.startswith("* "):
        return f"• {line[2:]}"
    return line
