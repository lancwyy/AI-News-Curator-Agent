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
