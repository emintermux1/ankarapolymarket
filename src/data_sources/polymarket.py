from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

from src.config import Settings
from src.data_sources.base import HttpSource, SourceError
from src.data_sources.schemas import MarketOutcome, MarketSnapshot, SourceHealth, SourceState


class PolymarketAviationReader(HttpSource):
    source_name = "Polymarket"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.gamma_events_url = "https://gamma-api.polymarket.com/events"
        self.clob_book_url = "https://clob.polymarket.com/book"
        self.data_trades_url = "https://data-api.polymarket.com/trades"

    async def get_market(self, target_date: date | None = None) -> MarketSnapshot | None:
        payload = await self._request_json(
            self.gamma_events_url,
            params={"slug": self.settings.polymarket_event_slug},
        )
        if not isinstance(payload, list) or not payload:
            return None
        event = payload[0]
        if not isinstance(event, dict):
            raise SourceError(self.source_name, "Gamma event payload is invalid")

        event_date = _extract_date_from_event(event)
        valid, message = self._validate_event(event, target_date or event_date)
        outcomes = []
        for market in event.get("markets") or []:
            if not isinstance(market, dict):
                continue
            outcomes.append(await self._parse_outcome(market))

        return MarketSnapshot(
            fetch_timestamp=datetime.now(timezone.utc),
            event_id=str(event.get("id") or ""),
            title=str(event.get("title") or ""),
            slug=str(event.get("slug") or self.settings.polymarket_event_slug),
            target_date=event_date,
            active=bool(event.get("active")),
            closed=bool(event.get("closed")),
            valid_for_target=valid,
            validation_message=message,
            link=f"https://polymarket.com/event/{event.get('slug') or self.settings.polymarket_event_slug}",
            resolution_source=event.get("resolutionSource"),
            liquidity=_safe_float(event.get("liquidity")),
            volume=_safe_float(event.get("volume")),
            outcomes=outcomes,
            raw_json=event,
        )

    async def health(self) -> SourceHealth:
        started = datetime.now(timezone.utc)
        try:
            snapshot = await self.get_market()
            latency = (datetime.now(timezone.utc) - started).total_seconds() * 1000
            if snapshot is None:
                return SourceHealth(source=self.source_name, state=SourceState.UNAVAILABLE, latency_ms=latency, message="ilgili market bulunamadı")
            state = SourceState.OK if snapshot.valid_for_target else SourceState.DEGRADED
            return SourceHealth(source=self.source_name, state=state, latency_ms=latency, message=snapshot.validation_message)
        except Exception as exc:
            return SourceHealth(source=self.source_name, state=SourceState.DOWN, message=str(exc))

    def _validate_event(self, event: dict[str, Any], target_date: date | None) -> tuple[bool, str | None]:
        haystack = " ".join(
            str(event.get(key) or "")
            for key in ("title", "description", "resolutionSource", "slug")
        ).lower()
        terms = [term.lower() for term in self.settings.polymarket_target_location_terms]
        has_location = any(term in haystack for term in terms)
        has_airport_source = any(term in haystack for term in ("esenboğa", "esenboga", "ltac", "esenbo"))
        event_date = _extract_date_from_event(event)
        date_ok = target_date is None or event_date is None or event_date == target_date
        active_ok = bool(event.get("active")) and not bool(event.get("closed"))
        if not has_location:
            return False, "market title/source Ankara/LTAC terms do not match"
        if not has_airport_source:
            return False, "resolution source does not explicitly mention Esenboğa/LTAC"
        if not date_ok:
            return False, f"market date {event_date} does not match target {target_date}"
        if not active_ok:
            return False, "market is not active"
        return True, None

    async def _parse_outcome(self, market: dict[str, Any]) -> MarketOutcome:
        prices = _parse_json_list(market.get("outcomePrices"))
        tokens = _parse_json_list(market.get("clobTokenIds"))
        question = str(market.get("question") or "")
        yes_token = str(tokens[0]) if len(tokens) >= 1 else None
        no_token = str(tokens[1]) if len(tokens) >= 2 else None
        best_bid = None
        best_ask = None
        spread = None
        if yes_token:
            try:
                book = await self._request_json(self.clob_book_url, params={"token_id": yes_token})
                bids = _parse_book_levels(book.get("bids") if isinstance(book, dict) else None)
                asks = _parse_book_levels(book.get("asks") if isinstance(book, dict) else None)
                best_bid = max((level["price"] for level in bids), default=None)
                best_ask = min((level["price"] for level in asks), default=None)
                if best_bid is not None and best_ask is not None:
                    spread = max(0.0, best_ask - best_bid)
            except Exception as exc:
                self.logger.warning("book fetch failed for %s: %s", yes_token, exc)

        trades = []
        condition_id = market.get("conditionId")
        if condition_id:
            try:
                raw_trades = await self._request_json(
                    self.data_trades_url,
                    params={"market": condition_id, "limit": 8},
                )
                if isinstance(raw_trades, list):
                    trades = [trade for trade in raw_trades if isinstance(trade, dict)]
            except Exception as exc:
                self.logger.warning("trade fetch failed for %s: %s", condition_id, exc)

        return MarketOutcome(
            question=question,
            bracket=_extract_bracket(question),
            condition_id=str(condition_id) if condition_id else None,
            yes_token_id=yes_token,
            no_token_id=no_token,
            yes_price=_safe_float(prices[0]) if len(prices) >= 1 else None,
            no_price=_safe_float(prices[1]) if len(prices) >= 2 else None,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            liquidity=_safe_float(market.get("liquidity")),
            volume=_safe_float(market.get("volume")),
            recent_trades=trades,
        )


def _parse_json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _parse_book_levels(value: Any) -> list[dict[str, float]]:
    if not isinstance(value, list):
        return []
    levels = []
    for row in value:
        if not isinstance(row, dict):
            continue
        price = _safe_float(row.get("price"))
        size = _safe_float(row.get("size"))
        if price is not None and size is not None:
            levels.append({"price": price, "size": size})
    return levels


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_bracket(question: str) -> str:
    match = re.search(r"be\s+(.+?)\s+on\s+", question, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return question.strip()


def _extract_date_from_event(event: dict[str, Any]) -> date | None:
    text = " ".join(str(event.get(key) or "") for key in ("title", "description", "slug"))
    explicit = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
    if explicit:
        return date(int(explicit.group(1)), int(explicit.group(2)), int(explicit.group(3)))
    month_match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}).*?(20\d{2})?",
        text,
        flags=re.IGNORECASE,
    )
    if month_match:
        month_names = [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
        year = int(month_match.group(3) or date.today().year)
        month = month_names.index(month_match.group(1).lower()) + 1
        return date(year, month, int(month_match.group(2)))
    return None

