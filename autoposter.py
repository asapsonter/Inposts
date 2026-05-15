#!/usr/bin/env python3
"""
LinkedIn Auto-Poster
====================
Fetches hot tech news from RSS feeds, generates a LinkedIn post via
Ollama + DeepSeek R1, and publishes it to LinkedIn.
"""

import argparse
import logging
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.logging import RichHandler
from logging.handlers import RotatingFileHandler

from config import config
from db import init_db, mark_seen
from rss import fetch_articles, score_articles, deduplicate
from llm import generate_post
from social import publish_to_linkedin

console = Console()

def setup_logging():
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "autoposter.log"
    
    verbose_format = "%(asctime)s [%(name)s] [%(levelname)s] %(message)s"
    
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2)
    file_handler.setFormatter(logging.Formatter(verbose_format))
    
    rich_handler = RichHandler(rich_tracebacks=True, markup=True)
    rich_handler.setFormatter(logging.Formatter("%(message)s"))

    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, rich_handler]
    )

log = logging.getLogger("autoposter.main")

def fetch_and_rank():
    """Fetch fresh, unseen articles and rank them by hotness.
    Returns (articles, conn). Caller is responsible for closing the conn."""
    conn = init_db()

    log.info("[bold green]=== Fetching articles ===[/bold green]")
    articles = fetch_articles()
    log.info(
        f"Fetched {len(articles)} articles within the last "
        f"{config.hotness_window_hours}h"
    )

    articles = deduplicate(articles, conn)
    log.info(f"{len(articles)} new (unseen) articles after dedup")

    if articles:
        articles = score_articles(articles)
    return articles, conn


def generate_and_publish(
    articles: list[dict],
    conn,
    selected_index: int | None = None,
    dry_run: bool = True,
) -> dict:
    """Generate a post from the ranked article list and (optionally) publish it.

    If selected_index is given, the post is written about THAT article and
    its image is attached. Otherwise, the LLM picks an article and we use
    the LLM's choice for both the post body and the image.
    """
    # Generate post — LLM tells us which article it picked (or we tell it)
    log.info("[bold green]=== Generating LinkedIn post ===[/bold green]")
    post_text, picked_index = generate_post(
        articles, selected_index=selected_index
    )
    picked_article = articles[picked_index]
    log.info(
        "Post is about: [bold]%s[/bold] (%s)",
        picked_article["title"], picked_article["source"],
    )

    console.rule("[bold cyan]GENERATED LINKEDIN POST[/bold cyan]")
    console.print(post_text)
    console.rule()

    # Image always comes from the picked article — never substitute another
    # article's image, otherwise the visual and the body would describe
    # different stories.
    image_url = picked_article.get("image_url")
    image_alt = picked_article["title"]
    if image_url:
        log.info("Attaching image: %s", image_url)
    else:
        log.warning(
            "Picked article has no image — posting text only "
            "(no cross-article fallback)."
        )

    # Only mark the article we actually used. The previous behaviour marked
    # the top 20 every run, which burned through a day's worth of feed items
    # after just a few generations.
    mark_seen(
        conn, picked_article["link"], picked_article["title"], picked_article["source"]
    )

    result = {
        "post_text": post_text,
        "picked_index": picked_index,
        "picked_title": picked_article["title"],
        "image_url": image_url,
        "posted": False,
        "post_id": None,
    }

    if dry_run:
        log.info("Dry-run mode — post was NOT published to LinkedIn.")
        log.info("Re-run with --post to publish.")
        return result

    post_id = publish_to_linkedin(
        post_text, image_url=image_url, image_alt=image_alt
    )
    from db import save_post
    if post_id:
        save_post(conn, post_text, post_id)
    result["posted"] = True
    result["post_id"] = post_id
    return result


def run_pipeline(dry_run: bool = True, selected_index: int | None = None):
    """Execute the full fetch → rank → generate → post pipeline."""
    articles, conn = fetch_and_rank()
    try:
        if not articles:
            log.warning("No new articles found. Nothing to do.")
            return

        # Print top articles
        log.info("[bold green]=== Top articles ===[/bold green]")
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", style="dim", width=3)
        table.add_column("Score", justify="right")
        table.add_column("Source", style="cyan")
        table.add_column("Title")
        for i, a in enumerate(articles[:10], 1):
            table.add_row(str(i), f"{a['score']:.4f}", a["source"], a["title"])
        console.print(table)

        generate_and_publish(
            articles, conn,
            selected_index=selected_index,
            dry_run=dry_run,
        )
    finally:
        conn.close()

def fetch_only():
    """Fetch & rank articles without calling the LLM or posting."""
    conn = init_db()
    articles = fetch_articles()
    articles = deduplicate(articles, conn)
    articles = score_articles(articles)

    table = Table(title="Fetched Articles", show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=4)
    table.add_column("Score", justify="right")
    table.add_column("Source", style="cyan", width=22)
    table.add_column("Title")

    for i, a in enumerate(articles[:25], 1):
        table.add_row(str(i), f"{a['score']:.4f}", a["source"], a["title"][:80])
        
    console.print(table)
    conn.close()

def loop_forever(dry_run: bool = True):
    """Run the scheduler loop in CLI mode, reading settings from the same
    `schedule` SQLite row that the UI writes to. The UI is the single
    source of truth — edit the schedule there.

    Note: do NOT run this alongside `uvicorn app:app`, otherwise both
    schedulers will fire and you'll get double-posts.
    """
    import asyncio
    import scheduler
    if dry_run:
        log.warning(
            "Dry-run is not supported when running the scheduler "
            "(scheduler always publishes). Use --fetch-only for dry-run."
        )
    log.info(
        "Scheduler loop starting — reading config from the schedule table. "
        "Edit at http://localhost:5050 (Schedule card). Ctrl+C to stop."
    )
    try:
        asyncio.run(scheduler.scheduler_loop())
    except KeyboardInterrupt:
        log.info("Scheduler stopped by user.")

def main():
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Fetch hot tech news → generate LinkedIn post via DeepSeek R1 → publish."
    )
    parser.add_argument(
        "--post",
        action="store_true",
        help="Actually publish to LinkedIn (default is dry-run).",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run in a continuous loop, posting once per day.",
    )
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Only fetch and rank articles (no LLM, no posting).",
    )
    args = parser.parse_args()

    if args.fetch_only:
        fetch_only()
    elif args.loop:
        loop_forever(dry_run=not args.post)
    else:
        run_pipeline(dry_run=not args.post)


if __name__ == "__main__":
    main()
