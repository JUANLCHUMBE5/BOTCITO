from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

import requests

from bot.config import NewsConfig


POSITIVE_TERMS = {
    "surge", "rally", "bullish", "breakout", "approval", "partnership",
    "adoption", "growth", "record", "gain", "wins", "upside",
}
NEGATIVE_TERMS = {
    "lawsuit", "ban", "hack", "crash", "bearish", "drop", "sell-off",
    "decline", "fraud", "risk", "loss", "liquidation", "investigation",
    "rejected", "delay",
}


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published_at: str
    sentiment_score: float
    summary: str


@dataclass
class NewsSnapshot:
    checked_at: datetime
    items: list[NewsItem]
    average_score: float
    should_block_buy: bool
    reason: str


class NewsService:
    def __init__(self, config: NewsConfig) -> None:
        self.config = config
        self.logger = logging.getLogger("news")
        self._last_snapshot: NewsSnapshot | None = None
        self._last_check_at: datetime | None = None

    def get_snapshot(self) -> NewsSnapshot | None:
        if not self.config.enabled or not self.config.api_key:
            return None

        now = datetime.now(timezone.utc)
        if (
            self._last_snapshot is not None
            and self._last_check_at is not None
            and now - self._last_check_at < timedelta(minutes=self.config.check_interval_minutes)
        ):
            return self._last_snapshot

        items = self._fetch_news()
        average_score = round(
            sum(item.sentiment_score for item in items) / len(items), 2
        ) if items else 0.0
        should_block = (
            self.config.block_on_negative_news
            and items
            and average_score <= self.config.negative_threshold
        )
        reason = (
            f"Noticias negativas detectadas (score={average_score})"
            if should_block
            else f"Contexto de noticias estable (score={average_score})"
        )
        self._last_check_at = now
        self._last_snapshot = NewsSnapshot(
            checked_at=now,
            items=items,
            average_score=average_score,
            should_block_buy=should_block,
            reason=reason,
        )
        return self._last_snapshot

    def _fetch_news(self) -> list[NewsItem]:
        try:
            if self.config.provider == "newsapi":
                payload = self._fetch_newsapi()
            else:
                payload = self._fetch_gnews()
        except requests.RequestException as exc:
            self.logger.warning("No se pudieron consultar noticias: %s", exc)
            return []

        articles = payload.get("articles", [])[: self.config.max_headlines]
        items: list[NewsItem] = []
        for article in articles:
            title = (article.get("title") or "").strip()
            description = (article.get("description") or "").strip()
            content = f"{title} {description}".lower()
            score = self._score_sentiment(content)
            items.append(
                NewsItem(
                    title=title or "Sin título",
                    url=article.get("url", ""),
                    source=(article.get("source") or {}).get("name", "desconocido"),
                    published_at=article.get("publishedAt", ""),
                    sentiment_score=score,
                    summary=description[:220],
                )
            )
        return items

    def _fetch_gnews(self) -> dict:
        response = requests.get(
            "https://gnews.io/api/v4/search",
            params={
                "q": self.config.query,
                "lang": self.config.language,
                "country": self.config.country,
                "max": self.config.max_headlines,
                "apikey": self.config.api_key,
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def _fetch_newsapi(self) -> dict:
        response = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": self.config.query,
                "language": self.config.language,
                "pageSize": self.config.max_headlines,
                "sortBy": "publishedAt",
            },
            headers={"X-Api-Key": self.config.api_key},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def _score_sentiment(self, text: str) -> float:
        positive_hits = sum(1 for word in POSITIVE_TERMS if word in text)
        negative_hits = sum(1 for word in NEGATIVE_TERMS if word in text)
        if positive_hits == 0 and negative_hits == 0:
            return 0.0
        raw_score = (positive_hits - negative_hits) / max(positive_hits + negative_hits, 1)
        return round(raw_score, 2)
