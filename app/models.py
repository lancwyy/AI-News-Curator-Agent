"""
models.py — Data models for the AI News Search Agent.

Defines Pydantic schemas used across the application for
article data transfer and validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class RawArticle(BaseModel):
    """An article as retrieved from an external source, before summarization."""

    title: str
    url: str
    content: str = ""          # full text or abstract
    source: str = ""           # e.g. "arxiv", "hackernews", "rss"
    published_at: Optional[datetime] = None


class Article(BaseModel):
    """A fully-processed article with an LLM-generated summary."""

    id: Optional[int] = None
    title: str
    summary: str = ""
    url: str
    source: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True
