"""
agent.py — AIResearchAgent: the core agent orchestrator.

Responsible for:
  1. Searching external sources (arXiv, Hacker News, RSS feeds)
  2. Summarizing articles via TextRank (No LLM for search results)
  3. Persisting results to SQLite
  4. Generating blog articles from selected sources via Google Gemini (Sequential)
"""

from __future__ import annotations

import logging
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import google.generativeai as genai
import nltk
from openai import OpenAI
from anthropic import Anthropic
from groq import Groq
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.text_rank import TextRankSummarizer

from app.database import save_article, get_articles_by_ids, get_setting
from app.models import Article, RawArticle
from app.search_sources import search_all_sources

logger = logging.getLogger(__name__)

# Default queries used for the "automatic search" feature.
DEFAULT_QUERIES = ["AI", "large language model", "AI agent"]

# Ensure NLTK data is available for sumy
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt')

class AIResearchAgent:
    """
    Core research agent.
    - Initial summaries: Local TextRank (No LLM usage for speed & cost)
    - Blog generation: Selectable LLM (Gemini, OpenAI, Claude)
    """

    def __init__(self) -> None:
        # Simple lock to ensure sequential LLM requests if multiple generation calls occur
        self._llm_lock = asyncio.Lock()

    def _get_api_key(self, provider_key: str, env_var: str) -> str:
        """Fetch key from DB first, then fallback to environment."""
        db_key = get_setting(provider_key)
        if db_key:
            return db_key
        return os.getenv(env_var, "")

    def _get_gemini_model(self):
        gemini_key = self._get_api_key("GOOGLE_API_KEY", "GOOGLE_API_KEY")
        if gemini_key:
            genai.configure(api_key=gemini_key)
            return genai.GenerativeModel("gemini-2.0-flash")
        return None

    def _get_openai_client(self):
        openai_key = self._get_api_key("OPENAI_API_KEY", "OPENAI_API_KEY")
        return OpenAI(api_key=openai_key) if openai_key else None

    def _get_anthropic_client(self):
        anthropic_key = self._get_api_key("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
        return Anthropic(api_key=anthropic_key) if anthropic_key else None

    def _get_groq_client(self):
        groq_key = self._get_api_key("GROQ_API_KEY", "GROQ_API_KEY")
        return Groq(api_key=groq_key) if groq_key else None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run(self, query: Optional[str] = None, days_back: Optional[int] = None) -> List[Article]:
        """Main orchestration: search → summarize (Local TextRank) → save → return."""
        raw_articles = await self.search_sources(query, days_back=days_back)

        processed: List[Article] = []
        for raw in raw_articles:
            # Non-LLM summarization to save tokens and time during search
            summary = self.summarize_article(raw)
            article = Article(
                title=raw.title,
                summary=summary,
                url=raw.url,
                source=raw.source,
            )
            saved = self.save_article(article)
            processed.append(saved)

        logger.info(
            "Agent run complete — %d articles processed (query=%s, days_back=%s)",
            len(processed),
            query,
            days_back,
        )
        return processed

    async def generate_blog_article(self, article_ids: List[int], model_provider: str = "gemini") -> str:
        """
        Research selected articles and generate a blog post.
        Uses the selected LLM provider (gemini, openai, claude) sequentially.
        """
        articles = get_articles_by_ids(article_ids)
        if not articles:
            return {"status": "error", "message": "No articles found for the given IDs.", "prompt": ""}

        # Prepare context
        context = "\n\n".join([
            f"Title: {a.title}\nSource: {a.source}\nSummary: {a.summary}\nURL: {a.url}"
            for a in articles
        ])

        prompt = (
            "You are a professional tech blogger. Research the following articles "
            "and write a high-quality, engaging blog post that synthesizes their "
            "key points. Quote from the articles where appropriate and provide "
            "insights into the technical trends they represent.\n\n"
            "Articles to reference:\n"
            f"{context}\n\n"
            "Requirements:\n"
            "1. Use a professional and engaging tone.\n"
            "2. Include a compelling title.\n"
            "3. Structure with headers (Markdown).\n"
            "4. Cite the sources using the provided URLs.\n"
            "5. Write in Traditional Chinese (繁體中文)."
        )

        # Prepare paths early for error logging
        today_str = datetime.now().strftime("%Y-%m-%d")
        base_dir = Path("/app/blog_article") / today_str
        timestamp = int(datetime.now().timestamp())
        prompt_filename = f"blog_{model_provider}_{timestamp}_prompt.txt"
        prompt_path = base_dir / prompt_filename

        async with self._llm_lock:
            try:
                content = ""
                if model_provider == "gemini":
                    gemini_model = self._get_gemini_model()
                    if not gemini_model:
                        return {"status": "error", "message": "GOOGLE_API_KEY not set.", "prompt": prompt}
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(None, lambda: gemini_model.generate_content(prompt))
                    content = response.text
                
                elif model_provider == "openai":
                    openai_client = self._get_openai_client()
                    if not openai_client:
                        return {"status": "error", "message": "OPENAI_API_KEY not set.", "prompt": prompt}
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(None, lambda: openai_client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": prompt}]
                    ))
                    content = response.choices[0].message.content
                
                elif model_provider == "claude":
                    anthropic_client = self._get_anthropic_client()
                    if not anthropic_client:
                        return {"status": "error", "message": "ANTHROPIC_API_KEY not set.", "prompt": prompt}
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(None, lambda: anthropic_client.messages.create(
                        model="claude-3-5-sonnet-20241022",
                        max_tokens=4096,
                        messages=[{"role": "user", "content": prompt}]
                    ))
                    content = response.content[0].text
                
                elif model_provider == "groq":
                    groq_client = self._get_groq_client()
                    if not groq_client:
                        return {"status": "error", "message": "GROQ_API_KEY not set.", "prompt": prompt}
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(None, lambda: groq_client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[{"role": "user", "content": prompt}]
                    ))
                    content = response.choices[0].message.content
                
                else:
                    return {"status": "error", "message": f"Unknown model provider '{model_provider}'", "prompt": prompt}

                # Directory logic for success
                base_dir.mkdir(parents=True, exist_ok=True)
                filename = f"blog_{model_provider}_{timestamp}.md"
                file_path = base_dir / filename
                
                # Save the generated content
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                
                # Save the input prompt for audit
                with open(prompt_path, "w", encoding="utf-8") as f:
                    f.write(prompt)

                logger.info("Generated blog article saved to %s (and prompt to %s)", file_path, prompt_path)
                return {"status": "success", "file_path": str(file_path)}

            except Exception as exc:
                logger.error("Failed to generate blog article: %s", exc)
                
                # Create directory and log error/prompt for troubleshooting
                try:
                    base_dir.mkdir(parents=True, exist_ok=True)
                    error_filename = f"blog_{model_provider}_{timestamp}_error.txt"
                    error_path = base_dir / error_filename
                    
                    with open(prompt_path, "w", encoding="utf-8") as f:
                        f.write(prompt)
                    with open(error_path, "w", encoding="utf-8") as f:
                        f.write(str(exc))
                    
                    logger.info("Error and prompt logged to %s and %s", error_path, prompt_path)
                except Exception as log_exc:
                    logger.error("Failed to log error to disk: %s", log_exc)

                return {
                    "status": "error",
                    "message": str(exc),
                    "prompt": prompt
                }

    # ------------------------------------------------------------------
    # Step 1: Search
    # ------------------------------------------------------------------

    async def search_sources(
        self, query: Optional[str] = None, days_back: Optional[int] = None
    ) -> List[RawArticle]:
        """
        Retrieve articles from all configured sources and filter by date if requested.
        """
        queries = [query] if query else DEFAULT_QUERIES
        all_articles: List[RawArticle] = []
        seen_urls: set[str] = set()

        # Calculate cutoff date if days_back is provided
        cutoff_date = None
        if days_back is not None:
            from datetime import timedelta
            # Using UTC to match model's Field(default_factory=datetime.utcnow)
            cutoff_date = datetime.utcnow() - timedelta(days=days_back)
            logger.info("Filtering articles published after %s (%d days back)", cutoff_date, days_back)

        for q in queries:
            results = await search_all_sources(q, max_per_source=10 if query else 5)
            for article in results:
                if article.url not in seen_urls:
                    # Apply date filter
                    if cutoff_date and article.published_at:
                        if article.published_at < cutoff_date:
                            continue
                    
                    seen_urls.add(article.url)
                    all_articles.append(article)

        return all_articles

    # ------------------------------------------------------------------
    # Step 2: Summarize (Local TextRank)
    # ------------------------------------------------------------------

    def summarize_article(self, article: RawArticle) -> str:
        """
        Generate a concise summary without using any LLM.
        Priority: 
        1. Existing content/description (if long enough)
        2. TextRank extraction via sumy
        """
        # If the article already provides a decent summary or description
        content_len = len(article.content.strip())
        if 100 <= content_len <= 800:
            return article.content.strip()

        # If content is too long or non-existent, use TextRank
        if content_len > 100:
            return self._textrank_summarize(article.content, sentence_count=3)
        
        return article.content.strip() or "No summary available."

    @staticmethod
    def _textrank_summarize(text: str, sentence_count: int = 3) -> str:
        """Perform extractive summarization using TextRank algorithm."""
        try:
            parser = PlaintextParser.from_string(text, Tokenizer("english"))
            summarizer = TextRankSummarizer()
            summary_sentences = summarizer(parser.document, sentence_count)
            return " ".join([str(s) for s in summary_sentences])
        except Exception as exc:
            logger.warning("TextRank summarization failed: %s", exc)
            # Simple fallback
            return text[:300].rsplit(" ", 1)[0] + "..." if len(text) > 300 else text

    # ------------------------------------------------------------------
    # Step 3: Save
    # ------------------------------------------------------------------

    @staticmethod
    def save_article(article: Article) -> Article:
        """Persist an article to SQLite."""
        return save_article(article)
