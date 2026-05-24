from __future__ import annotations

from datetime import date

import pytest

from src.config import Settings
from src.data_sources.havaforum import HavaForumScraper


def _article(post_id: str, published: str, text: str, author: str = "Tester") -> str:
    return f"""
    <article class="wbbPost message" data-post-id="{post_id}">
        <meta itemprop="datePublished" content="{published}">
        <meta itemprop="url" content="https://forum.havaforum.com/thread/8893/?postID={post_id}#post{post_id}">
        <span itemprop="name">{author}</span>
        <div class="messageText" itemprop="text"><p>{text}</p></div>
    </article>
    """


@pytest.mark.asyncio
async def test_havaforum_selects_same_day_and_previous_day_tomorrow_posts(monkeypatch) -> None:
    adapter = HavaForumScraper(
        Settings(
            TELEGRAM_ADMIN_IDS="",
            HAVAFORUM_THREAD_URL="https://forum.havaforum.com/thread/8893-ankara/",
            HAVAFORUM_PAGE_WINDOW=1,
        )
    )
    html = f"""
    <woltlab-core-pagination page="545" count="545" url="https://forum.havaforum.com/thread/8893-ankara/"></woltlab-core-pagination>
    {_article("1", "2026-05-23T20:08:22+03:00", "Yarın yine öğleden sonra etkili kütleler görebiliriz.")}
    {_article("2", "2026-05-24T11:51:18+03:00", "Etimesgutta göz gözü görmüyor bolca şimşek aktivitesi var.")}
    {_article("3", "2026-05-23T15:51:47+03:00", "Dikmende sağlam yağış var.")}
    """

    async def fake_request_text(url: str, **kwargs):
        return html

    monkeypatch.setattr(adapter, "_request_text", fake_request_text)

    analysis = await adapter.get_analysis(date(2026, 5, 24))

    assert analysis.post_count == 2
    assert analysis.same_day_post_count == 1
    assert analysis.previous_day_tomorrow_post_count == 1
    assert analysis.signals["yağış"] == 1
    assert analysis.signals["şimşek/oraj"] == 1
    assert "Etimesgut" in analysis.locations


@pytest.mark.asyncio
async def test_havaforum_fetches_last_page_when_seed_url_is_first_page(monkeypatch) -> None:
    adapter = HavaForumScraper(
        Settings(
            TELEGRAM_ADMIN_IDS="",
            HAVAFORUM_THREAD_URL="https://forum.havaforum.com/thread/8893-ankara/",
            HAVAFORUM_PAGE_WINDOW=1,
        )
    )
    first_page = """
    <woltlab-core-pagination page="1" count="545" url="https://forum.havaforum.com/thread/8893-ankara/"></woltlab-core-pagination>
    """
    last_page = _article("2", "2026-05-24T11:51:18+03:00", "Batıkentte çok kuvvetli yağış var.")
    requested: list[str] = []

    async def fake_request_text(url: str, **kwargs):
        requested.append(url)
        if "pageNo=545" in url:
            return last_page
        return first_page

    monkeypatch.setattr(adapter, "_request_text", fake_request_text)

    analysis = await adapter.get_analysis(date(2026, 5, 24))

    assert any("pageNo=545" in url for url in requested)
    assert analysis.post_count == 1
    assert "Batıkent" in analysis.locations
