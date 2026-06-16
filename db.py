import sqlite3
import datetime
from pathlib import Path
from config import config

# Valid schedule modes. "hourly" fires every N hours from the previous run.
# "daily_at" fires every N days at a specific HH:MM clock time.
SCHEDULE_MODES = ("hourly", "daily_at")


def init_db() -> sqlite3.Connection:
    """Create / open the SQLite database and ensure the table exists."""
    conn = sqlite3.connect(config.db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_articles (
            url       TEXT PRIMARY KEY,
            title     TEXT,
            source    TEXT,
            seen_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            linkedin_id TEXT,
            content     TEXT,
            image_url   TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    # Migration for databases created before image_url existed. SQLite's
    # ALTER TABLE raises if the column is already there — swallow that case.
    try:
        conn.execute("ALTER TABLE posts ADD COLUMN image_url TEXT")
    except sqlite3.OperationalError:
        pass
    # Single-row table (id always = 1) holding the live posting schedule.
    # UI edits go here, scheduler + CLI loop both read from here.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            mode            TEXT    NOT NULL DEFAULT 'daily_at',
            interval_hours  INTEGER NOT NULL DEFAULT 6,
            interval_days   INTEGER NOT NULL DEFAULT 1,
            post_hour       INTEGER NOT NULL DEFAULT 11,
            post_minute     INTEGER NOT NULL DEFAULT 0,
            enabled         INTEGER NOT NULL DEFAULT 0,
            last_run_at     TEXT,
            next_run_at     TEXT
        )
    """)
    # Seed the row on first run using config.py values as initial defaults.
    seed_hour = config.post_hour if 0 <= config.post_hour <= 23 else 11
    seed_minute = config.post_minute if 0 <= config.post_minute <= 59 else 0
    conn.execute(
        """INSERT OR IGNORE INTO schedule
           (id, mode, post_hour, post_minute)
           VALUES (1, 'daily_at', ?, ?)""",
        (seed_hour, seed_minute),
    )
    # Cleanup data older than 30 days to prevent bloat
    conn.execute("DELETE FROM seen_articles WHERE seen_at < datetime('now', '-30 days')")
    conn.commit()
    return conn


def get_schedule(conn: sqlite3.Connection) -> dict:
    """Return the current schedule row as a dict."""
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM schedule WHERE id = 1").fetchone()
    if row is None:
        raise RuntimeError("schedule row missing — init_db should have seeded it")
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    return d


def update_schedule(
    conn: sqlite3.Connection,
    *,
    mode: str | None = None,
    interval_hours: int | None = None,
    interval_days: int | None = None,
    post_hour: int | None = None,
    post_minute: int | None = None,
    enabled: bool | None = None,
    last_run_at: str | None = None,
    next_run_at: str | None = None,
) -> dict:
    """Partial-update the schedule row, validating supplied fields."""
    if mode is not None and mode not in SCHEDULE_MODES:
        raise ValueError(f"mode must be one of {SCHEDULE_MODES}, got {mode!r}")
    if interval_hours is not None and interval_hours < 1:
        raise ValueError("interval_hours must be >= 1")
    if interval_days is not None and interval_days < 1:
        raise ValueError("interval_days must be >= 1")
    if post_hour is not None and not (0 <= post_hour <= 23):
        raise ValueError("post_hour must be 0..23")
    if post_minute is not None and not (0 <= post_minute <= 59):
        raise ValueError("post_minute must be 0..59")

    fields = {
        "mode": mode,
        "interval_hours": interval_hours,
        "interval_days": interval_days,
        "post_hour": post_hour,
        "post_minute": post_minute,
        "enabled": (1 if enabled else 0) if enabled is not None else None,
        "last_run_at": last_run_at,
        "next_run_at": next_run_at,
    }
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return get_schedule(conn)

    setters = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE schedule SET {setters} WHERE id = 1",
        list(updates.values()),
    )
    conn.commit()
    return get_schedule(conn)

def is_seen(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen_articles WHERE url = ?", (url,)).fetchone()
    return row is not None

def mark_seen(conn: sqlite3.Connection, url: str, title: str, source: str):
    conn.execute(
        "INSERT OR IGNORE INTO seen_articles (url, title, source) VALUES (?, ?, ?)",
        (url, title, source),
    )
    conn.commit()

def save_post(
    conn: sqlite3.Connection,
    content: str,
    linkedin_id: str,
    image_url: str | None = None,
):
    conn.execute(
        "INSERT INTO posts (linkedin_id, content, image_url) VALUES (?, ?, ?)",
        (linkedin_id, content, image_url),
    )
    conn.commit()

def get_posts(conn: sqlite3.Connection):
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM posts ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def get_latest_post_time(conn: sqlite3.Connection) -> str | None:
    """Return the `created_at` of the most recent post, as an ISO-ish string
    ('YYYY-MM-DD HH:MM:SS'), or None if there are no posts."""
    row = conn.execute("SELECT MAX(created_at) FROM posts").fetchone()
    return row[0] if row and row[0] else None

def delete_posts(conn: sqlite3.Connection, ids: list[int]) -> int: 
    """delete catalog post by ID
    remove dashboard record, it does not affect linkedin
    """
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute( 
                       f"DELETE FROM posts WHERE id IN ({placeholders})", list(ids)
                       )
    conn.commit()
    return cur.rowcount 
