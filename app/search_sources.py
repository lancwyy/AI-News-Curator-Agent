"""
search_sources.py — External data source integrations.

Provides async functions to search arXiv, Hacker News, and AI-related
RSS feeds.  Each function returns a list of RawArticle dicts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import List
from urllib.parse import quote_plus

import feedparser
import httpx

from app.models import RawArticle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# arXiv API
# ---------------------------------------------------------------------------

ARXIV_API_URL = "https://export.arxiv.org/api/query"

# Rate-limit: arXiv asks for at least 3 seconds between requests.
_last_arxiv_call: float = 0.0
_ARXIV_INTERVAL: float = 3.0


async def search_arxiv(query: str, max_results: int = 5) -> List[RawArticle]:
    """Search the arXiv Atom feed API and return matching papers."""
    global _last_arxiv_call

    # Respect arXiv rate limit — wait if last call was less than 3s ago
    elapsed = time.monotonic() - _last_arxiv_call
    if _last_arxiv_call > 0 and elapsed < _ARXIV_INTERVAL:
        wait_time = _ARXIV_INTERVAL - elapsed
        logger.info("arXiv rate limit: waiting %.1fs before next request", wait_time)
        await asyncio.sleep(wait_time)

    params = {
        "search_query": f"all:{quote_plus(query)}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }

    articles: List[RawArticle] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(ARXIV_API_URL, params=params)
            _last_arxiv_call = time.monotonic()
            resp.raise_for_status()

        feed = feedparser.parse(resp.text)
        for entry in feed.entries:
            articles.append(
                RawArticle(
                    title=entry.get("title", "").strip().replace("\n", " "),
                    url=entry.get("link", ""),
                    content=entry.get("summary", "").strip(),
                    source="arxiv",
                )
            )
    except Exception as exc:
        _last_arxiv_call = time.monotonic()
        logger.warning("arXiv search failed for query '%s': %s", query, exc)

    return articles


# ---------------------------------------------------------------------------
# Hacker News (Algolia search API)
# ---------------------------------------------------------------------------

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"


async def search_hackernews(query: str, max_results: int = 5) -> List[RawArticle]:
    """Search Hacker News via the Algolia API."""
    params = {
        "query": query,
        "tags": "story",
        "hitsPerPage": max_results,
    }

    articles: List[RawArticle] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(HN_SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        for hit in data.get("hits", []):
            title = hit.get("title", "")
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            # Use the story text if available, otherwise fall back to title
            content = hit.get("story_text") or hit.get("title", "")
            articles.append(
                RawArticle(
                    title=title,
                    url=url,
                    content=content,
                    source="hackernews",
                )
            )
    except Exception as exc:
        logger.warning("Hacker News search failed for query '%s': %s", query, exc)

    return articles


# ---------------------------------------------------------------------------
# RSS Feeds (AI blogs)
# ---------------------------------------------------------------------------

# Curated list of AI-related RSS feeds.
AI_RSS_FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://blog.google/rss/",
    "https://techcrunch.com/feed/",
]


async def search_rss_feeds(
    query: str, max_results: int = 5
) -> List[RawArticle]:
    """Fetch AI blog RSS feeds and filter entries by the query string."""
    query_lower = query.lower()
    articles: List[RawArticle] = []

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for feed_url in AI_RSS_FEEDS:
            try:
                resp = await client.get(feed_url)
                resp.raise_for_status()
                feed = feedparser.parse(resp.text)

                for entry in feed.entries:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    # Simple keyword matching — keeps things lightweight
                    if query_lower in title.lower() or query_lower in summary.lower():
                        articles.append(
                            RawArticle(
                                title=title.strip(),
                                url=entry.get("link", ""),
                                content=summary.strip(),
                                source="rss",
                            )
                        )
                        if len(articles) >= max_results:
                            return articles
            except Exception as exc:
                logger.warning("RSS fetch failed for %s: %s", feed_url, exc)

    return articles[:max_results]


# ---------------------------------------------------------------------------
# Unified search helper
# ---------------------------------------------------------------------------


async def search_all_sources(
    query: str, max_per_source: int = 5
) -> List[RawArticle]:
    """Aggregate results from all configured sources for a single query."""
    results: List[RawArticle] = []

    # Run searches concurrently-ish but keep error isolation
    arxiv_results = await search_arxiv(query, max_per_source)
    hn_results = await search_hackernews(query, max_per_source)
    rss_results = await search_rss_feeds(query, max_per_source)

    results.extend(arxiv_results)
    results.extend(hn_results)
    results.extend(rss_results)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: List[RawArticle] = []
    for article in results:
        if article.url not in seen_urls:
            seen_urls.add(article.url)
            unique.append(article)

    return unique
