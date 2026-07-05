"""
Configuration for LinkedIn Auto-Poster.
Managed by Pydantic Settings for type safety and validation.
"""

from pathlib import Path
from typing import List, Dict, Any
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- SQLite ---
    db_path: str = str(Path(__file__).parent / "seen_articles.db")

    # --- Ollama / DeepSeek R1 ---
    ollama_url: str = "http://localhost:11434/api/generate"
    ollama_model: str = "deepseek-r1"
    ollama_timeout: int = 120

    # --- LinkedIn API v2 ---
    linkedin_access_token: str = ""
    linkedin_author_urn: str = ""  # e.g. urn:li:person:XXXXXXXXXX

    # --- Scheduling (initial seed values only) ---
    # Actual live schedule is in the `schedule` table in SQLite, editable
    # via the dashboard UI. These values only matter on first run, before
    # the row exists. Keep them valid: post_hour 0..23, post_minute 0..59.
    post_hour: int = 11
    post_minute: int = 0

    # --- Misc ---
    max_articles: int = 20
    hotness_window_hours: int = 24

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )


config = Settings()

# --- RSS Feeds ---
RSS_FEEDS: List[Dict[str, Any]] = [
    # {
    #     "name": "Hacker News",
    #     "url": "https://hnrss.org/best",
    #     "has_comments": True,
    # },
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "has_comments": False,
    },
    # {
    #     "name": "Wired (Top Stories)",
    #     "url": "https://www.wired.com/feed/rss",
    #     "has_comments": False,
    # },
    # {
    #     "name": "Ars Technica",
    #     "url": "https://feeds.arstechnica.com/arstechnica/index",
    #     "has_comments": False,
    # },
    {
        "name": "Text Next 24",
        "url": "https://technext24.com/feed/",
        "has_comments": False,
    },
    {
        "name": "Tech Cabal",
        "url": "https://techcabal.com/feed/",
        "has_comments": False,
    },
    {
        "name": "tech point africa",
        "url": "https://techpoint.africa/feed/",
        "has_comments": False,
    },
    {
        "name": "tech economy africa",
        "url": "https://techeconomy.ng/feed/",
        "has_comments": False,
    },
    {
        "name": "gizmodo",
        "url": "https://gizmodo.com/feed/",
        "has_comments": False,
    },
    {
        "name": "engadget",
        "url": "https://engadget.com/feed/",
        "has_comments": False,
    },

]
