from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import SourceHealth, SourceState, TwitterPost


class TwitterXAdapter(HttpSource):
    source_name = "Twitter_X"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def get_latest_posts(self, limit: int = 5) -> list[dict[str, Any]]:
        """Return list of {text, timestamp, url} from MGM Ankara Nitter mirror."""
        fetch_timestamp = datetime.now(timezone.utc)
        posts: list[dict[str, Any]] = []
        for account in self.settings.twitter_ankara_accounts:
            url = f"https://nitter.net/{account}"
            try:
                html = await self._request_text(url)
                parsed = _parse_nitter_posts(html, account, limit)
                posts.extend(parsed)
            except Exception as exc:
                self.logger.warning("Failed to fetch Nitter posts for %s: %s", account, exc)
                continue
        return posts[:limit]

    async def get_twitter_snapshots(self, limit: int = 5) -> list[TwitterPost]:
        fetch_timestamp = datetime.now(timezone.utc)
        posts = await self.get_latest_posts(limit=limit)
        return [
            TwitterPost(
                fetch_timestamp=fetch_timestamp,
                post_id=post.get("post_id", f"tw-{i}"),
                author=post.get("author", "mgm_ankara"),
                text=post.get("text", ""),
                published_at=post.get("timestamp") or fetch_timestamp,
                url=post.get("url", ""),
            )
            for i, post in enumerate(posts)
        ]

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            account = self.settings.twitter_ankara_accounts[0] if self.settings.twitter_ankara_accounts else "mgm_ankara"
            url = f"https://nitter.net/{account}"
            html = await self._request_text(url)
            posts = _parse_nitter_posts(html, account, limit=1)
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if posts:
                return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
            return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message="Nitter erişilebilir ancak post parse edilemedi")
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))


def _parse_nitter_posts(html: str, account: str, limit: int = 5) -> list[dict[str, Any]]:
    """Parse posts from Nitter HTML."""
    posts: list[dict[str, Any]] = []
    # Match full tweet containers (tweet-body div) to capture content, date, and permalink together
    tweet_containers = re.findall(
        r'<div\s+class="tweet-body[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>\s*</div>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not tweet_containers:
        tweet_containers = re.findall(
            r'<div\s+class="tweet-content[^"]*"[^>]*>(.*?)</div>',
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

    for i, container in enumerate(tweet_containers[:limit]):
        # Extract text from tweet-content within the full container
        content_match = re.search(
            r'<div\s+class="tweet-content[^"]*"[^>]*>(.*?)</div>',
            container,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = _clean_tweet_text(content_match.group(1)) if content_match else _clean_tweet_text(container)
        if not text or len(text) < 5:
            continue
        # Try to extract timestamp from full container context
        timestamp_match = re.search(
            r'<span\s+class="tweet-date"[^>]*>.*?<a[^>]*title="([^"]+)"',
            container,
        )
        ts = None
        if timestamp_match:
            try:
                ts = datetime.fromisoformat(timestamp_match.group(1).replace("Z", "+00:00"))
            except ValueError:
                ts = None

        # Try to extract permalink from full container context
        url_match = re.search(r'<a[^>]*href="(/[^/]+/status/\d+[^"]*)"', container)
        url = f"https://nitter.net{url_match.group(1)}" if url_match else f"https://nitter.net/{account}"

        post_id_match = re.search(r"/status/(\d+)", url)
        post_id = post_id_match.group(1) if post_id_match else f"tw-{i}"

        posts.append({
            "post_id": post_id,
            "author": account,
            "text": text,
            "timestamp": ts or datetime.now(timezone.utc),
            "url": url,
        })

    # Nitter v2 fallback parsing
    if not posts:
        body_pattern = re.findall(
            r'<div\s+class="timeline-item[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        for block in body_pattern[:limit]:
            text_match = re.search(r'<div\s+class="tweet-content[^"]*"[^>]*>(.*?)(?:</div>|$)', block, re.DOTALL)
            if text_match:
                text = _clean_tweet_text(text_match.group(0))
                if text and len(text) >= 5:
                    posts.append({
                        "post_id": f"tw-v2-{len(posts)}",
                        "author": account,
                        "text": text,
                        "timestamp": datetime.now(timezone.utc),
                        "url": f"https://nitter.net/{account}",
                    })

    return posts


def _clean_tweet_text(raw: str) -> str:
    """Strip HTML tags and entities from tweet content."""
    text = re.sub(r"<br\s*/?>", " ", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#\d+;", "", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return " ".join(line for line in lines if line).strip()
