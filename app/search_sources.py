"""
search_sources.py — External data source integrations.

Provides async functions to search arXiv, Hacker News, and AI-related
RSS feeds.  Each function returns a list of RawArticle dicts.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
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
            # Extract published date
            published_at = None
            if "published_parsed" in entry:
                published_at = datetime.fromtimestamp(time.mktime(entry.published_parsed))

            articles.append(
                RawArticle(
                    title=entry.get("title", "").strip().replace("\n", " "),
                    url=entry.get("link", ""),
                    content=entry.get("summary", "").strip(),
                    source="arxiv",
                    published_at=published_at,
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
            
            published_at = None
            if "created_at_i" in hit:
                published_at = datetime.fromtimestamp(hit["created_at_i"])

            articles.append(
                RawArticle(
                    title=title,
                    url=url,
                    content=content,
                    source="hackernews",
                    published_at=published_at,
                )
            )
    except Exception as exc:
        logger.warning("Hacker News search failed for query '%s': %s", query, exc)

    return articles


# ---------------------------------------------------------------------------
# RSS Feeds (AI & Tech blogs)
# ---------------------------------------------------------------------------

# Curated list of AI-related and major tech RSS feeds.
AI_RSS_FEEDS = [
    # --- AI Labs & Companies ---
    "https://openai.com/news/rss.xml",              # OpenAI
    "https://deepmind.google/blog/rss.xml",          # Google DeepMind
    "https://blog.google/rss/",                      # Google Blog
    # --- Major Tech Media ---
    "https://techcrunch.com/feed/",                   # TechCrunch
    "https://www.wired.com/feed/rss",                 # Wired
    "https://feeds.arstechnica.com/arstechnica/index", # Ars Technica
    "https://www.theverge.com/rss/index.xml",         # The Verge
    "https://venturebeat.com/feed/",                  # VentureBeat
    "https://www.technologyreview.com/feed/",         # MIT Technology Review
    "https://www.zdnet.com/topic/artificial-intelligence/rss.xml",  # ZDNet AI
    # --- AI-Focused Publications ---
    "https://www.artificialintelligence-news.com/feed/",  # AI News
    "https://machinelearningmastery.com/feed/",       # Machine Learning Mastery
    "https://www.marktechpost.com/feed/",             # MarkTechPost
    "https://syncedreview.com/feed/",                 # Synced (AI research)
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
                
                matched_in_feed = 0

                for entry in feed.entries:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    # Simple keyword matching — keeps things lightweight
                    if query_lower in title.lower() or query_lower in summary.lower():
                        published_at = None
                        if "published_parsed" in entry:
                            published_at = datetime.fromtimestamp(time.mktime(entry.published_parsed))
                        elif "updated_parsed" in entry:
                            published_at = datetime.fromtimestamp(time.mktime(entry.updated_parsed))

                        articles.append(
                            RawArticle(
                                title=title.strip(),
                                url=entry.get("link", ""),
                                content=summary.strip(),
                                source="rss",
                                published_at=published_at,
                            )
                        )
                        matched_in_feed += 1
                        
                        if matched_in_feed >= max_results:
                            logger.info("RSS feed '%s' returned %d matches (reached per-feed max of %d)", feed_url, matched_in_feed, max_results)
                            break
                
                if matched_in_feed < max_results:
                    logger.info("RSS feed '%s' returned %d matches for query '%s'", feed_url, matched_in_feed, query)
            except Exception as exc:
                logger.warning("RSS fetch failed for %s: %s", feed_url, exc)

    return articles


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
    logger.info("Search '%s': arXiv returned %d results", query, len(arxiv_results))
    
    hn_results = await search_hackernews(query, max_per_source)
    logger.info("Search '%s': HackerNews returned %d results", query, len(hn_results))
    
    rss_results = await search_rss_feeds(query, max_per_source)
    logger.info("Search '%s': RSS (14 feeds) returned %d results", query, len(rss_results))

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

    logger.info("Search '%s': Total %d unique results collected across all sources", query, len(unique))
    return unique
