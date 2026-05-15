import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles

import autoposter
import scheduler
from db import init_db, get_posts, get_schedule, update_schedule

log = logging.getLogger("autoposter.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background scheduler when uvicorn boots, stop it when
    uvicorn shuts down. Scheduler runs only while this process is alive."""
    # Ensure the DB and schedule row exist before the scheduler reads them.
    init_db().close()
    task = asyncio.create_task(scheduler.scheduler_loop())
    log.info("Scheduler task started")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        log.info("Scheduler task stopped")


app = FastAPI(title="LinkedIn Auto-Poster Dashboard", lifespan=lifespan)


@app.get("/api/posts")
def get_all_posts():
    conn = init_db()
    posts = get_posts(conn)
    conn.close()
    return {"posts": posts}


@app.get("/api/articles")
def list_articles():
    """Return the current ranked list of fresh articles for the UI picker."""
    articles, conn = autoposter.fetch_and_rank()
    conn.close()
    return {
        "articles": [
            {
                "index": i,
                "title": a["title"],
                "source": a["source"],
                "score": a["score"],
                "link": a["link"],
                "image_url": a.get("image_url"),
                "summary": a["summary"],
                "comment_count": a["comment_count"],
            }
            for i, a in enumerate(articles[:20])
        ]
    }


@app.post("/api/trigger")
def trigger_generation(payload: dict | None = Body(default=None)):
    """Manually generate and publish a post.

    Body (optional): {"article_link": "<url>"}
        - When provided, the post is written about THAT specific article.
        - When omitted, the LLM picks the hottest article itself.
    """
    article_link = (payload or {}).get("article_link")

    try:
        articles, conn = autoposter.fetch_and_rank()
        try:
            if not articles:
                raise HTTPException(
                    status_code=404,
                    detail="No new articles available right now.",
                )

            selected_index = None
            if article_link:
                for i, a in enumerate(articles):
                    if a["link"] == article_link:
                        selected_index = i
                        break
                if selected_index is None:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Selected article is no longer available. "
                            "Please refresh and pick again."
                        ),
                    )

            result = autoposter.generate_and_publish(
                articles, conn,
                selected_index=selected_index,
                dry_run=False,
            )
        finally:
            conn.close()

        return {
            "status": "success",
            "message": "Post generated and published successfully.",
            "picked_title": result.get("picked_title"),
            "post_id": result.get("post_id"),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------------------------------
# Schedule endpoints — the UI is the only place that writes here.
# --------------------------------------------------------------------------

@app.get("/api/schedule")
def read_schedule():
    """Return the current schedule plus the computed next-run timestamp."""
    conn = init_db()
    try:
        sched = scheduler.refresh_next_run(conn) if get_schedule(conn)["enabled"] else get_schedule(conn)
    finally:
        conn.close()
    return {"schedule": sched}


@app.put("/api/schedule")
def write_schedule(payload: dict = Body(...)):
    """Update the schedule. All fields optional; supplied fields validated."""
    conn = init_db()
    try:
        sched = update_schedule(
            conn,
            mode=payload.get("mode"),
            interval_hours=payload.get("interval_hours"),
            interval_days=payload.get("interval_days"),
            post_hour=payload.get("post_hour"),
            post_minute=payload.get("post_minute"),
            enabled=payload.get("enabled"),
        )
        # Recompute next_run_at immediately so the UI sees the new value
        # without waiting for the scheduler's next tick.
        sched = scheduler.refresh_next_run(conn) if sched["enabled"] else sched
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()
    # Kick the running scheduler so it picks up the new settings now
    # instead of after its current sleep finishes (up to TICK_SECONDS).
    scheduler.request_wake()
    return {"schedule": sched}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
