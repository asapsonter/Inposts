"""
Sync the dashboard schedule into the user's system crontab.

Called from PUT /api/schedule. The crontab entry runs `autoposter.py --post`
(a single immediate post) at the time the UI schedule describes, so unattended
posting works even when this web app isn't running.

The entry is wrapped in marker lines so we can find/replace/remove it
idempotently without touching the user's other cron jobs.
"""

import logging 
import subprocess
import sys
from pathlib import Path 

log = logging.getLogger("autoposter.cron")

PROJECT_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = PROJECT_DIR / "autoposter.py"
PYTHON_BIN = Path(sys.executable) # the venv python running app
LOG_PATH = "/tmp/autoposter.log"


MARKER_BEGIN = "# >>> inposts-autoposter (managed by dashboard) >>>"
MARKER_END = "# <<< inposts-autoposter <<<"

class CronUnavailable(RuntimeError):
    """Raised when we can't safely read/write the crontab."""

def _read_crontab() -> str:
    try:
        res = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except FileNotFoundError:
        raise CronUnavailable("`crontab` is not available on this system")
    if res.returncode == 0:
        return res.stdout
    # No crontab yet is fine (empty). Any other failure: abort so we never
    # clobber the user's existing cron entries by writing a partial file.
    if "no crontab" in (res.stderr or "").lower():
        return ""
    raise CronUnavailable(f"`crontab -l` failed: {res.stderr.strip()}")

def _write_crontab(content: str) -> None:
    content = content.rstrip("\n")
    if content:
        content += "\n"     # crontab requires a trailing newline
    res = subprocess.run(["crontab", "-"], input=content, text=True,
                         capture_output=True)
    if res.returncode != 0:
        raise CronUnavailable(f"writing crontab failed: {res.stderr.strip()}")   

def _strip_managed(crontab: str) -> str:
    """Remove our managed block AND any stray/legacy `--post` lines so we
    never end up posting twice."""
    out, inside = [], False
    for line in crontab.splitlines():
        s = line.strip()
        if s == MARKER_BEGIN:
            inside = True
            continue
        if s == MARKER_END:
            inside = False
            continue
        if inside:
            continue
        if "autoposter.py --post" in line and not s.startswith("#"):
            continue                        # drop the old hand-written entry
        out.append(line)
    return "\n".join(out).strip("\n")

def _cron_expr(schedule: dict) -> str | None:
    """Translate a schedule row into a 5-field cron expression.
    Approximations cron can't express exactly:
      - 'every N days'  -> day-of-month step */N (resets each month)
      - 'every N hours' -> hour step */N (resets each day; use a divisor of 24)
    """
    mode = schedule["mode"]
    m = int(schedule.get("post_minute") or 0)
    h = int(schedule.get("post_hour") or 0)
    if mode == "daily_at":
        days = max(1, int(schedule.get("interval_days") or 1))
        return f"{m} {h} * * *" if days == 1 else f"{m} {h} */{days} * *"
    if mode == "hourly":
        n = max(1, min(int(schedule.get("interval_hours") or 1), 23))
        return f"{m} */{n} * * *"
    return None

def sync_cron(schedule: dict) -> None:
    """Rewrite the managed crontab block to match `schedule`.

    Enabled  -> install/replace the entry at the schedule's time.
    Disabled -> remove the entry entirely.
    Safe to call anywhere; failures are logged, never raised to the caller.
    """
    try:
        base = _strip_managed(_read_crontab())

        if not schedule.get("enabled"):
            _write_crontab(base)
            log.info("Schedule disabled — removed managed cron entry.")
            return

        expr = _cron_expr(schedule)
        if not expr:
            log.warning("Unknown schedule mode %r — cron not updated.",
                        schedule.get("mode"))
            _write_crontab(base)
            return

        line = (f"{expr} cd {PROJECT_DIR} && {PYTHON_BIN} {SCRIPT_PATH} "
                f"--post >> {LOG_PATH} 2>&1")
        block = f"{MARKER_BEGIN}\n{line}\n{MARKER_END}"
        new_content = (base + "\n" if base else "") + block
        _write_crontab(new_content)
        log.info("Cron entry updated -> %s", expr)
    except CronUnavailable as e:
        log.warning("Could not sync crontab: %s", e)
    except Exception:
        log.exception("Unexpected error syncing crontab")