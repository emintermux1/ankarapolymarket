from __future__ import annotations

import re
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

from src.config import Settings
from src.data_sources.base import HttpSource
from src.data_sources.schemas import ForumAnalysis, ForumPost, SourceHealth, SourceState


_SIGNAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "yağış": ("yagis", "yagmur", "saganak", "yagdi", "yagiyor", "kutle", "ekolu"),
    "kuvvetli yağış": ("kuvvetli", "siddetli", "kirmizi", "turuncu", "sel", "su baskini"),
    "şimşek/oraj": ("simsek", "yildirim", "gok gur", "oraj", "gumluyor", "gumluyo"),
    "dolu": ("dolu",),
    "sel": ("sel", "su baskini"),
    "rüzgâr": ("ruzgar", "firtina"),
    "sıcak": ("sicak", "isinma"),
    "serin": ("serin", "soguk"),
}

_ANKARA_LOCATIONS = [
    "Akyurt",
    "Altındağ",
    "Ayaş",
    "Bala",
    "Batıkent",
    "Beypazarı",
    "Çamlıdere",
    "Çankaya",
    "Çubuk",
    "Dikmen",
    "Elmadağ",
    "Elvankent",
    "Etimesgut",
    "Gölbaşı",
    "Haymana",
    "Kalecik",
    "Kazan",
    "Keçiören",
    "Kızılcahamam",
    "Kurtboğazı",
    "Mamak",
    "Nallıhan",
    "Polatlı",
    "Pursaklar",
    "Sincan",
    "Şereflikoçhisar",
    "Yaşamkent",
    "Yenimahalle",
]


class HavaForumScraper(HttpSource):
    source_name = "HavaForum"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.tz = ZoneInfo(settings.report_timezone)

    async def get_analysis(self, target_date: date) -> ForumAnalysis:
        fetch_timestamp = datetime.now(timezone.utc)
        page_html = await self._fetch_relevant_pages()
        posts_by_id: dict[str, ForumPost] = {}
        for page_url, html in page_html.items():
            for post in _parse_posts(html, page_url):
                posts_by_id.setdefault(post.post_id, post)
        selected = self._select_target_posts(list(posts_by_id.values()), target_date)
        return _build_analysis(
            fetch_timestamp=fetch_timestamp,
            target_date=target_date,
            thread_url=self.settings.havaforum_thread_url,
            posts=selected,
            tz=self.tz,
        )

    async def health(self) -> SourceHealth:
        if not self.settings.havaforum_thread_url:
            return SourceHealth(source=self.source_name, state=SourceState.UNAVAILABLE, message="HAVAFORUM_THREAD_URL not configured")
        started = time.perf_counter()
        try:
            html = await self._request_text(self.settings.havaforum_thread_url)
            latency = (time.perf_counter() - started) * 1000
            posts = _parse_posts(html, self.settings.havaforum_thread_url)
            if not posts:
                return SourceHealth(source=self.source_name, state=SourceState.DEGRADED, latency_ms=latency, message="forum post parse edilemedi")
            return SourceHealth(source=self.source_name, state=SourceState.OK, latency_ms=latency)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))

    async def _fetch_relevant_pages(self) -> dict[str, str]:
        initial_url = self.settings.havaforum_thread_url
        first_html = await self._request_text(initial_url)
        base_url, current_page, page_count = _pagination_info(first_html, initial_url)
        urls = {initial_url}
        if page_count is not None:
            pages = set(_page_range(page_count, self.settings.havaforum_page_window))
            if current_page is not None:
                pages.update(_page_range(current_page, self.settings.havaforum_page_window, page_count))
            urls.update(_page_url(base_url, page) for page in pages)
        result = {initial_url: first_html}
        for url in sorted(urls - {initial_url}):
            result[url] = await self._request_text(url)
        return result

    def _select_target_posts(self, posts: list[ForumPost], target_date: date) -> list[ForumPost]:
        selected: list[ForumPost] = []
        previous_day = target_date - timedelta(days=1)
        for post in sorted(posts, key=lambda item: item.published_at):
            local_date = post.published_at.astimezone(self.tz).date()
            same_day = local_date == target_date
            previous_day_tomorrow = (
                self.settings.havaforum_include_previous_day_tomorrow_posts
                and local_date == previous_day
                and _mentions_tomorrow(post.text)
            )
            if same_day or previous_day_tomorrow:
                selected.append(post.model_copy(update={"matches_target_context": True}))
        return selected[-40:]


def _pagination_info(html: str, initial_url: str) -> tuple[str, int | None, int | None]:
    tag_match = re.search(r"<woltlab-core-pagination\b[^>]*>", html, flags=re.IGNORECASE)
    current_page = _page_number_from_url(initial_url)
    page_count: int | None = None
    base_url = _base_page_url(initial_url)
    if tag_match:
        tag = tag_match.group(0)
        page = _attr(tag, "page")
        count = _attr(tag, "count")
        url = _attr(tag, "url")
        if page:
            current_page = int(page)
        if count:
            page_count = int(count)
        if url:
            base_url = unescape(url)
    return base_url, current_page, page_count


def _page_range(anchor: int, window: int, limit: int | None = None) -> range:
    end = min(anchor, limit) if limit is not None else anchor
    start = max(1, end - window + 1)
    return range(start, end + 1)


def _page_url(base_url: str, page: int) -> str:
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if page <= 1:
        query.pop("pageNo", None)
    else:
        query["pageNo"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _base_page_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query.pop("pageNo", None)
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _page_number_from_url(url: str) -> int | None:
    values = parse_qs(urlparse(url).query).get("pageNo")
    if not values:
        return None
    try:
        return int(values[0])
    except ValueError:
        return None


def _parse_posts(html: str, page_url: str) -> list[ForumPost]:
    posts: list[ForumPost] = []
    for match in re.finditer(r"<article\b(?=[^>]*\bwbbPost\b)(?P<attrs>[^>]*)>(?P<body>.*?)</article>", html, flags=re.IGNORECASE | re.DOTALL):
        attrs = match.group("attrs")
        body = match.group("body")
        post_id = _attr(attrs, "data-post-id")
        published = _meta_content(body, "datePublished")
        text = _message_text(body)
        if not post_id or not published or not text:
            continue
        try:
            published_at = datetime.fromisoformat(published)
        except ValueError:
            continue
        url = _meta_content(body, "url") or urljoin(page_url, f"#post{post_id}")
        author = _author_name(body)
        posts.append(
            ForumPost(
                post_id=post_id,
                url=url,
                author=author,
                published_at=published_at,
                text=text,
            )
        )
    return posts


def _build_analysis(
    *,
    fetch_timestamp: datetime,
    target_date: date,
    thread_url: str,
    posts: list[ForumPost],
    tz: ZoneInfo,
) -> ForumAnalysis:
    same_day = 0
    previous_day_tomorrow = 0
    previous_day = target_date - timedelta(days=1)
    locations = _ranked_locations(posts)
    signals = _signal_counts(posts)
    for post in posts:
        local_date = post.published_at.astimezone(tz).date()
        if local_date == target_date:
            same_day += 1
        elif local_date == previous_day and _mentions_tomorrow(post.text):
            previous_day_tomorrow += 1
    latest = max((post.published_at for post in posts), default=None)
    if posts:
        summary = _summary_text(len(posts), same_day, previous_day_tomorrow, locations, signals)
        unavailable = None
    else:
        summary = "Hedef günle ilişkili forum mesajı bulunamadı."
        unavailable = "hedef günle ilişkili forum mesajı yok"
    return ForumAnalysis(
        fetch_timestamp=fetch_timestamp,
        target_date=target_date,
        thread_url=thread_url,
        posts=posts,
        same_day_post_count=same_day,
        previous_day_tomorrow_post_count=previous_day_tomorrow,
        latest_post_at=latest,
        locations=locations,
        signals=signals,
        summary=summary,
        unavailable_reason=unavailable,
    )


def _summary_text(
    post_count: int,
    same_day: int,
    previous_day_tomorrow: int,
    locations: list[str],
    signals: dict[str, int],
) -> str:
    buckets = [f"{post_count} hedef gün bağlantılı mesaj"]
    detail = []
    if same_day:
        detail.append(f"{same_day} aynı gün")
    if previous_day_tomorrow:
        detail.append(f"{previous_day_tomorrow} önceki gün 'yarın' bağlamı")
    if detail:
        buckets.append(f"({', '.join(detail)})")
    if signals:
        signal_text = ", ".join(f"{name} {count}" for name, count in list(signals.items())[:4])
        buckets.append(f"sinyal: {signal_text}")
    if locations:
        buckets.append(f"bölgeler: {', '.join(locations[:5])}")
    return "; ".join(buckets) + "."


def _signal_counts(posts: list[ForumPost]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, keywords in _SIGNAL_KEYWORDS.items():
        count = sum(1 for post in posts if any(keyword in _normalize(post.text) for keyword in keywords))
        if count:
            counts[label] = count
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _ranked_locations(posts: list[ForumPost]) -> list[str]:
    counts: Counter[str] = Counter()
    for post in posts:
        normalized_text = _normalize(post.text)
        for location in _ANKARA_LOCATIONS:
            if _normalize(location) in normalized_text:
                counts[location] += 1
    return [name for name, _ in counts.most_common()]


def _message_text(article_body: str) -> str:
    match = re.search(r'<div\s+class="messageText"\s+itemprop="text"\s*>(?P<text>.*?)</div>', article_body, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return _clean_html(match.group("text"))


def _author_name(article_body: str) -> str | None:
    match = re.search(r'<span\s+itemprop="name"\s*>(?P<name>.*?)</span>', article_body, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    name = _clean_html(match.group("name"))
    return name or None


def _meta_content(html: str, itemprop: str) -> str | None:
    pattern = rf'<meta\s+itemprop="{re.escape(itemprop)}"\s+content="(?P<value>[^"]+)"'
    match = re.search(pattern, html, flags=re.IGNORECASE)
    if not match:
        return None
    return unescape(match.group("value"))


def _attr(text: str, name: str) -> str | None:
    match = re.search(rf'\b{re.escape(name)}="(?P<value>[^"]*)"', text, flags=re.IGNORECASE)
    if not match:
        return None
    return unescape(match.group("value"))


def _clean_html(raw_html: str) -> str:
    value = re.sub(r"<(script|style)\b.*?</\1>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<woltlab-quote\b.*?</woltlab-quote>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<blockquote\b.*?</blockquote>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _mentions_tomorrow(text: str) -> bool:
    normalized = _normalize(text)
    return any(token in normalized for token in ("yarin", "yarina", "yarinki", "ertesi gun"))


def _normalize(text: str) -> str:
    return (
        text.casefold()
        .replace("ı", "i")
        .replace("ğ", "g")
        .replace("ü", "u")
        .replace("ş", "s")
        .replace("ö", "o")
        .replace("ç", "c")
    )
