"""
Background scheduler — the single executor of automatic posts.

Reads its config from the `schedule` row in SQLite (populated by the UI via
PUT /api/schedule). Runs inside uvicorn as an asyncio task started by
app.py's lifespan. The CLI `autoposter.py --loop` also reads from the same
schedule row, so the UI is the only place to change scheduling behavior.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from db import init_db, get_schedule, update_schedule

log = logging.getLogger("autoposter.scheduler")

# How often to wake up and re-check the schedule. Smaller = more responsive
# to UI changes; larger = less CPU. 60s is a reasonable balance for a
# minute-grained scheduler.
TICK_SECONDS = 60


def compute_next_run(schedule: dict, now: datetime) -> datetime:
    """Decide when the *next* run should fire given the current schedule
    and the timestamp of the last run.

    Pure function — no I/O. Easy to unit-test.
    """
    mode = schedule["mode"]
    last_run = _parse_iso(schedule.get("last_run_at"))

    if mode == "hourly":
        n = max(1, int(schedule.get("interval_hours") or 1))
        # If never run, fire one interval from "now". Otherwise from the last run.
        base = last_run if last_run else now
        nxt = base + timedelta(hours=n)
        # Catch-up: if the computed time is already in the past (e.g. the
        # server was offline), fire immediately rather than skipping.
        return max(nxt, now)

    if mode == "daily_at":
        n = max(1, int(schedule.get("interval_days") or 1))
        h = int(schedule.get("post_hour") or 0)
        m = int(schedule.get("post_minute") or 0)
        if last_run:
            target_day = (last_run + timedelta(days=n)).date()
        else:
            # First run: today at HH:MM if that's still in the future,
            # otherwise tomorrow.
            target_day = now.date()
        nxt = datetime.combine(target_day, datetime.min.time()).replace(
            hour=h, minute=m
        )
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt

    raise ValueError(f"Unknown schedule mode: {mode!r}")


def refresh_next_run(conn) -> dict:
    """Recompute next_run_at based on current settings + last_run_at,
    persist it, and return the fresh schedule row."""
    schedule = get_schedule(conn)
    if not schedule["enabled"]:
        return schedule
    now = datetime.now()
    nxt = compute_next_run(schedule, now)
    return update_schedule(conn, next_run_at=nxt.isoformat(timespec="seconds"))


async def scheduler_loop():
    """Forever-running async task. Wakes every TICK_SECONDS, checks the DB
    schedule, and fires a post when next_run_at <= now."""
    log.info("Scheduler started (tick=%ds)", TICK_SECONDS)
    # Lazy import — autoposter pulls in heavy deps we don't want at module load.
    import autoposter

    while True:
        try:
            conn = init_db()
            try:
                schedule = get_schedule(conn)
                if not schedule["enabled"]:
                    # Disabled — clear next_run for clarity and idle.
                    if schedule.get("next_run_at"):
                        update_schedule(conn, next_run_at="")
                else:
                    # Make sure next_run_at is up-to-date relative to current
                    # settings (someone may have just edited the schedule).
                    schedule = refresh_next_run(conn)
                    nxt = _parse_iso(schedule.get("next_run_at"))
                    now = datetime.now()
                    if nxt and nxt <= now:
                        log.info(
                            "Scheduler firing now (next_run_at=%s, mode=%s)",
                            schedule["next_run_at"], schedule["mode"],
                        )
                        await _fire_scheduled_run(conn, autoposter)
            finally:
                conn.close()
        except Exception:
            log.exception("Scheduler tick failed — will retry next tick")

        await asyncio.sleep(TICK_SECONDS)


async def _fire_scheduled_run(conn, autoposter_mod):
    """Run the fetch → generate → publish pipeline in a worker thread so
    we don't block the event loop with synchronous LinkedIn / Ollama I/O."""
    started_at = datetime.now()
    try:
        await asyncio.to_thread(_run_pipeline_safely, autoposter_mod)
    except Exception:
        log.exception("Scheduled pipeline raised")
    finally:
        # Always record last_run_at (even on failure) — otherwise a flaky
        # feed could cause the scheduler to fire-loop on every tick.
        finished_conn = init_db()
        try:
            update_schedule(
                finished_conn,
                last_run_at=started_at.isoformat(timespec="seconds"),
            )
            # Recompute next_run_at now that last_run_at moved forward.
            refresh_next_run(finished_conn)
        finally:
            finished_conn.close()


def _run_pipeline_safely(autoposter_mod):
    """Wrapper around autoposter.run_pipeline that LLM-picks the article.
    Runs in a worker thread (no asyncio context needed)."""
    articles, conn = autoposter_mod.fetch_and_rank()
    try:
        if not articles:
            log.warning("Scheduled run: no fresh articles available — skipping.")
            return
        autoposter_mod.generate_and_publish(articles, conn, dry_run=False)
    finally:
        conn.close()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None
