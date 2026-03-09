"""
database.py — SQLite persistence layer.

Provides helpers to initialise the database, save articles,
and retrieve stored articles.  Uses plain sqlite3 for simplicity.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List

from app.models import Article

# Database file location — stored inside the mounted volume so data
# persists across container restarts.
DB_PATH = Path("/app/data/articles.db")


def _get_connection() -> sqlite3.Connection:
    """Return a connection to the SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the articles table if it does not exist."""
    conn = _get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL,
            summary    TEXT NOT NULL DEFAULT '',
            url        TEXT NOT NULL,
            source     TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fetch_queries (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL
        )
        """
    )
    # Seed default queries if empty
    cursor = conn.execute("SELECT COUNT(*) FROM fetch_queries")
    if cursor.fetchone()[0] == 0:
        default_queries = ["AI", "large language model", "AI agent"]
        for q in default_queries:
            conn.execute("INSERT INTO fetch_queries (query) VALUES (?)", (q,))
    conn.commit()
    conn.close()


def save_article(article: Article) -> Article:
    """Persist an article and return it with the assigned id."""
    conn = _get_connection()
    cursor = conn.execute(
        """
        INSERT INTO articles (title, summary, url, source, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            article.title,
            article.summary,
            article.url,
            article.source,
            article.created_at.isoformat(),
        ),
    )
    article.id = cursor.lastrowid
    conn.commit()
    conn.close()
    return article


def get_articles(limit: int = 100) -> List[Article]:
    """Return the most recent articles, newest first."""
    conn = _get_connection()
    rows = conn.execute(
        "SELECT * FROM articles ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()

    articles: List[Article] = []
    for row in rows:
        articles.append(
            Article(
                id=row["id"],
                title=row["title"],
                summary=row["summary"],
                url=row["url"],
                source=row["source"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
        )
    return articles


def get_articles_by_ids(ids: List[int]) -> List[Article]:
    """Return specific articles by their IDs."""
    if not ids:
        return []

    conn = _get_connection()
    placeholders = ",".join(["?"] * len(ids))
    rows = conn.execute(
        f"SELECT * FROM articles WHERE id IN ({placeholders})", ids
    ).fetchall()
    conn.close()

    articles: List[Article] = []
    for row in rows:
        articles.append(
            Article(
                id=row["id"],
                title=row["title"],
                summary=row["summary"],
                url=row["url"],
                source=row["source"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
        )
    return articles


def delete_article(article_id: int) -> None:
    """Delete an article from the database."""
    conn = _get_connection()
    conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
    conn.commit()
    conn.close()


def delete_articles_bulk(article_ids: List[int]) -> None:
    """Delete multiple articles from the database."""
    if not article_ids:
        return
    conn = _get_connection()
    placeholders = ",".join(["?"] * len(article_ids))
    conn.execute(f"DELETE FROM articles WHERE id IN ({placeholders})", article_ids)
    conn.commit()
    conn.close()


def get_unique_article_dates() -> List[str]:
    """Return a list of unique dates (YYYY-MM-DD) where articles exist."""
    conn = _get_connection()
    # Subtracting the time part from created_at
    rows = conn.execute(
        "SELECT DISTINCT date(created_at) as article_date FROM articles ORDER BY article_date DESC"
    ).fetchall()
    conn.close()
    return [row["article_date"] for row in rows]


def get_articles_by_date(date_str: str) -> List[Article]:
    """Return all articles created on a specific date."""
    conn = _get_connection()
    rows = conn.execute(
        "SELECT * FROM articles WHERE date(created_at) = ? ORDER BY id DESC", (date_str,)
    ).fetchall()
    conn.close()

    articles: List[Article] = []
    for row in rows:
        articles.append(
            Article(
                id=row["id"],
                title=row["title"],
                summary=row["summary"],
                url=row["url"],
                source=row["source"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
        )
    return articles


# ---------------------------------------------------------------------------
# Settings Helpers
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    """Retrieve a setting value from the database."""
    conn = _get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    """Set a setting value in the database (upsert)."""
    conn = _get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fetch Queries Helpers
# ---------------------------------------------------------------------------

def get_fetch_queries() -> List[dict]:
    """Return the list of managed keywords for fetching news."""
    conn = _get_connection()
    rows = conn.execute("SELECT * FROM fetch_queries ORDER BY id ASC").fetchall()
    conn.close()
    return [{"id": row["id"], "query": row["query"]} for row in rows]


def add_fetch_query(query: str) -> int:
    """Add a new keyword to the fetch list."""
    conn = _get_connection()
    cursor = conn.execute("INSERT INTO fetch_queries (query) VALUES (?)", (query,))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


def delete_fetch_query(query_id: int) -> None:
    """Delete a keyword from the fetch list."""
    conn = _get_connection()
    conn.execute("DELETE FROM fetch_queries WHERE id = ?", (query_id,))
    conn.commit()
    conn.close()
