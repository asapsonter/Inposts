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

from db import init_db, get_schedule, update_schedule, get_latest_post_time

log = logging.getLogger("autoposter.scheduler")

# Maximum sleep between schedule re-checks. The loop also wakes immediately
# whenever request_wake() is called (e.g. from the PUT /api/schedule
# handler), so UI changes apply instantly even though this is set to 60s.
TICK_SECONDS = 60

# Module-level handles set by scheduler_loop() at startup. request_wake()
# uses these to nudge the running loop from any thread (FastAPI sync
# handlers run in a thread pool, not on the event loop).
_loop: asyncio.AbstractEventLoop | None = None
_wake_event: asyncio.Event | None = None


def request_wake() -> None:
    """Ask the scheduler to re-check its plan right now instead of waiting
    for the next tick. Safe to call from anywhere — including sync FastAPI
    handlers — because it dispatches the set() onto the scheduler's loop.

    If the scheduler isn't running (e.g. CLI mode hasn't started yet, or
    uvicorn isn't up), this is a no-op."""
    if _loop is None or _wake_event is None:
        return
    _loop.call_soon_threadsafe(_wake_event.set)


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
        # If the computed time is in the past (e.g. server was offline),
        # slide forward by whole intervals to the next future slot — do
        # NOT fire a backlog of missed posts on startup.
        while nxt <= now:
            nxt += timedelta(hours=n)
        return nxt

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
        # Same no-catch-up rule as hourly: slide by whole interval_days
        # steps so we land on a regular HH:MM slot in the future.
        while nxt <= now:
            nxt += timedelta(days=n)
        return nxt

    raise ValueError(f"Unknown schedule mode: {mode!r}")


def refresh_next_run(conn) -> dict:
    """Recompute next_run_at based on current settings + last_run_at,
    persist it, and return the fresh schedule row.

    Before computing, bump `last_run_at` to the timestamp of the most
    recent row in the `posts` table if that's newer than what's stored.
    The post history is the source of truth: if a post was made (by the
    scheduler, the UI, or the CLI), it counts as the last run."""
    schedule = get_schedule(conn)
    if not schedule["enabled"]:
        return schedule

    latest_post = _parse_post_timestamp(get_latest_post_time(conn))
    stored_last = _parse_iso(schedule.get("last_run_at"))
    if latest_post and (stored_last is None or latest_post > stored_last):
        schedule = update_schedule(
            conn,
            last_run_at=latest_post.isoformat(timespec="seconds"),
        )

    now = datetime.now()
    nxt = compute_next_run(schedule, now)
    return update_schedule(conn, next_run_at=nxt.isoformat(timespec="seconds"))


async def scheduler_loop():
    """Forever-running async task. Wakes every TICK_SECONDS *or* immediately
    when request_wake() is called, checks the DB schedule, and fires a post
    when next_run_at <= now."""
    global _loop, _wake_event
    _loop = asyncio.get_running_loop()
    _wake_event = asyncio.Event()
    log.info("Scheduler started (max tick=%ds, wake-on-change enabled)", TICK_SECONDS)
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

        # Sleep until either TICK_SECONDS elapses OR someone calls
        # request_wake() (e.g. the UI saved a new schedule). The wait_for
        # timeout caps the idle period; the event wakes us early when set.
        try:
            await asyncio.wait_for(_wake_event.wait(), timeout=TICK_SECONDS)
            log.debug("Scheduler woken by request_wake()")
        except asyncio.TimeoutError:
            pass
        finally:
            _wake_event.clear()


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


def _parse_post_timestamp(s: str | None) -> datetime | None:
    """Parse a `posts.created_at` value. SQLite's `datetime('now')` writes
    'YYYY-MM-DD HH:MM:SS'; older rows might be 'YYYY-MM-DDTHH:MM:SS'."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace(" ", "T"))
    except ValueError:
        return None
