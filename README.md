# InPosts — LinkedIn Auto-Poster

An automated tool that fetches the hottest African tech news daily, generates a professional LinkedIn post using a local AI model (Ollama + DeepSeek R1), attaches a story image, and publishes it to your LinkedIn profile — driven either from a browser dashboard or the CLI.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Getting LinkedIn API Credentials](#getting-linkedin-api-credentials)
- [Usage](#usage)
  - [Web UI Dashboard (Recommended)](#web-ui-dashboard-recommended)
  - [CLI Commands](#cli-commands)
- [Scheduling (Automated Posts)](#scheduling-automated-posts)
- [Customization](#customization)
- [Troubleshooting](#troubleshooting)

---

## Overview

InPosts automates the entire workflow of staying active on LinkedIn with quality tech content:

1. **Fetches** articles asynchronously from a configurable set of African tech RSS feeds (TechNext24, TechCabal, Techpoint Africa, Tech Economy)
2. **Ranks** them by a hotness score combining recency and (when available) comment-based popularity
3. **Deduplicates** using a local SQLite database so you never post about the same story twice
4. **Extracts an image** from each article's `media:content`, enclosures, or inline `<img>` tags
5. **Generates** a polished LinkedIn post using DeepSeek R1 running locally via Ollama (no cloud API costs)
6. **Uploads the image** to LinkedIn (bypassing WordPress hotlink protection with browser-like headers) and waits for it to reach `AVAILABLE` status
7. **Publishes** directly to your LinkedIn profile via the official LinkedIn REST API (`/rest/posts`)

A FastAPI app (`app.py`) exposes a small JSON API plus a static dashboard, and runs an in-process async scheduler that fires posts on the schedule you set in the UI.

---

## How It Works

```
RSS Feeds (configurable in config.py)
        |
        v   rss.py — async aiohttp fetch + feedparser
  Articles (last N hours, with image URL)
        |
        v   db.py — SQLite dedup against `seen_articles`
        |
        v   rss.score_articles — recency (60%) + popularity (40%)
        |
        v   llm.py — DeepSeek R1 via Ollama
  Either: LLM picks an article + writes the post
  Or:     UI/CLI picks a specific article + LLM writes about it
        |
        v   social.py — download image with browser UA,
                       upload to LinkedIn, wait for AVAILABLE
        |
        v   POST /rest/posts (LinkedIn REST API)
        |
        v   db.py — persist to `posts` table for history view
```

### Hotness Scoring (`rss.score_articles`)

Each article gets a score from 0 to 1:

- **Recency (60% weight):** Linear decay over the configured window (default 24h). A brand-new article scores 1.0; a 24h-old article scores 0.0.
- **Popularity (40% weight):** Based on comment count, normalized against the most-commented article in the batch. Feeds without comment data get 0 here, so the current African-tech feed set (no comment metadata) effectively ranks by recency alone — flip `has_comments: True` in `config.py` for any feed that exposes them.

### AI Post Generation (`llm.generate_post`)

Two paths:

- **LLM picks an article:** the top N (default 20) ranked articles are sent to DeepSeek R1, which returns an `INDEX:` line plus the post body. `llm.py` parses out the index and strips the model's `<think>…</think>` reasoning tags.
- **You pick an article:** the dashboard's "Choose Article" modal (or the `article_link` body field on `POST /api/trigger`) bypasses the picker and tells the LLM exactly which story to write about.

In both cases the post is constrained to: a catchy hook, 2–3 insightful sentences, 3 hashtags, and ≤300 words.

### Image Pipeline (`social.py`)

- The source image is downloaded with a Chrome-like `User-Agent` and a same-origin `Referer`. Most African tech sites run WordPress hotlink-protection plugins that 403 default `python-requests` traffic — this header set gets past them.
- LinkedIn's image upload is asynchronous. After `initializeUpload` + `PUT` of the bytes, `social.py` polls `GET /rest/images/{urn}` until status is `AVAILABLE` (15s timeout). Referencing a still-processing URN causes LinkedIn to silently drop the media from the published post, so this wait is required.
- If the image fails at any stage the post still publishes — text only.

---

## Project Structure

```
inposts/
├── app.py              # FastAPI app + JSON API + scheduler lifespan + static mount
├── autoposter.py       # Pipeline orchestrator + CLI entry (--fetch-only / --post / --loop)
├── scheduler.py        # Async scheduler loop — reads `schedule` row, fires runs
├── rss.py              # Async RSS fetching, image extraction, hotness scoring
├── llm.py              # Ollama / DeepSeek R1 client with tenacity retries
├── social.py           # LinkedIn REST publishing + image upload pipeline
├── db.py               # SQLite layer — seen_articles, posts, schedule tables
├── config.py           # Pydantic-Settings config + RSS_FEEDS list
├── routers/            # Legacy FastAPI routers (current app.py defines routes inline)
│   ├── __init__.py
│   ├── posts.py
│   └── tasks.py
├── static/             # Dashboard assets served at /
│   ├── index.html
│   ├── script.js
│   └── style.css
├── logs/               # Rotating log files (auto-created)
│   └── autoposter.log
├── requirements.txt    # Python dependencies
├── .env.example        # Template for credentials — copy to .env
├── .env                # Your actual credentials (gitignored)
├── .gitignore
├── LICENSE
├── seen_articles.db    # Auto-created SQLite database (gitignored)
├── venv/               # Python virtual environment (gitignored)
└── README.md           # This file
```

---

## Prerequisites

- **macOS** (Linux also works)
- **Python 3.10+**
- **Ollama** installed with DeepSeek R1 (or any chat model) pulled:
  ```bash
  # Install Ollama (if not already installed)
  brew install ollama

  # Pull the DeepSeek R1 model
  ollama pull deepseek-r1

  # Verify it's running
  curl http://localhost:11434/api/generate -d '{"model":"deepseek-r1","prompt":"Hi","stream":false}'
  ```
- **LinkedIn Developer App** with the "Share on LinkedIn" product enabled (see [Getting LinkedIn API Credentials](#getting-linkedin-api-credentials))

---

## Installation

```bash
# Clone or navigate to the project
cd ~/Documents/Projects/git_projects/inposts

# Create a virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

**Dependencies installed:**

| Package | Purpose |
|---|---|
| `requests` | Sync HTTP calls (Ollama, LinkedIn, image fetch) |
| `aiohttp` | Async RSS fetching (all feeds in parallel) |
| `feedparser` | RSS/Atom feed parsing |
| `python-dotenv` | Load credentials from `.env` |
| `pydantic-settings` | Typed config in `config.py` |
| `tenacity` | Retry decorators on Ollama + LinkedIn calls |
| `rich` | Pretty CLI output (tables, logging) |
| `fastapi` | Web API + dashboard backend |
| `uvicorn` | ASGI server |

All other modules used (`sqlite3`, `asyncio`, `json`, `re`, `argparse`, `logging`) are Python standard library.

---

## Configuration

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# Required — LinkedIn API credentials
LINKEDIN_ACCESS_TOKEN=your_access_token_here
LINKEDIN_AUTHOR_URN=urn:li:person:your_person_id_here

# Optional — override defaults
# OLLAMA_URL=http://localhost:11434/api/generate
# OLLAMA_MODEL=deepseek-r1
# OLLAMA_TIMEOUT=120
# DB_PATH=./seen_articles.db
# POST_HOUR=11           # Initial seed only — UI/DB owns the live schedule
# POST_MINUTE=0          # Initial seed only — UI/DB owns the live schedule
# MAX_ARTICLES=20
# HOTNESS_WINDOW_HOURS=24
```

Configuration is centralised in `config.py` via Pydantic-Settings. Anything in `.env` (or a regular shell env var) overrides the defaults. **The `POST_HOUR` / `POST_MINUTE` env values are only used to seed the SQLite `schedule` row on first run** — after that, edit the schedule from the dashboard. See [Scheduling](#scheduling-automated-posts).

---

## Getting LinkedIn API Credentials

This is a one-time setup. You need two values: an **Access Token** and your **Author URN**.

### Step 1: Create a LinkedIn App

1. Go to [LinkedIn Developer Apps](https://www.linkedin.com/developers/apps)
2. Click **Create App**
3. Fill in:
   - **App name:** anything (e.g., "InPosts Auto-Poster")
   - **LinkedIn Page:** select your company page, or create one
   - **Logo:** any image
4. Click **Create app**

### Step 2: Enable "Share on LinkedIn" Product

1. In your app, go to the **Products** tab
2. Find **Share on LinkedIn** and request access
3. This grants the `w_member_social` OAuth scope

### Step 3: Configure Redirect URL

1. Go to the **Auth** tab
2. Under **Authorized redirect URLs for your app**, add:
   ```
   http://localhost:8080/callback
   ```
3. Save

### Step 4: Get an Authorization Code

Open this URL in your browser, replacing `YOUR_CLIENT_ID` with your app's Client ID:

```
https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost:8080/callback&scope=w_member_social
```

- Sign in and click **Allow**
- Your browser will redirect to `http://localhost:8080/callback?code=AQXXXXX...`
- **The page will show a 404 error — this is expected**
- Copy the `code=` value from the URL bar

### Step 5: Exchange the Code for an Access Token

Run this in your terminal (replace the placeholders):

```bash
curl -X POST https://www.linkedin.com/oauth/v2/accessToken \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "code=PASTE_AUTH_CODE_HERE" \
  --data-urlencode "redirect_uri=http://localhost:8080/callback" \
  --data-urlencode "client_id=YOUR_CLIENT_ID" \
  --data-urlencode "client_secret=YOUR_CLIENT_SECRET"
```

This returns JSON like:
```json
{
  "access_token": "AQWz8v_jG...",
  "expires_in": 5183999,
  "scope": "w_member_social"
}
```

The `access_token` is valid for **60 days**. Put it in your `.env` file.

### Step 6: Find Your Author URN (Person ID)

Your person ID is NOT the same as your member ID. To find it:

1. Log into LinkedIn in your browser
2. Open **Developer Tools** (Cmd+Option+I on Mac)
3. Go to the **Elements** or **Sources** tab
4. Search (Cmd+F) for `urn:li:person:` in the page source
5. Copy the full URN (e.g., `urn:li:person:XXXXXXXXXX`)

Alternatively, search page source for `primaryEvaluationUrn` which contains `urn:li:member:XXXXXX`, then use the LinkedIn REST API error messages to resolve it to the `urn:li:person:` format (the API will tell you the correct person URN in its error response).

Put the URN in your `.env`:
```
LINKEDIN_AUTHOR_URN=urn:li:person:your_id_here
```

---

## Usage

### Web UI Dashboard (Recommended)

The primary way to use InPosts is through its browser dashboard, which lets you browse ranked articles, pick a specific one to post about, view past posts, and configure the auto-posting schedule — all without touching the terminal.

**Start the server** (run this from the project root, with Ollama already running):

```bash
venv/bin/uvicorn app:app --host 127.0.0.1 --port 5050 --log-level info
```

Then open **[http://localhost:5050](http://localhost:5050)** in your browser.

The dashboard shows:

- **Auto-posting schedule card** — at the top of the page. Flip the switch to enable, click **Edit schedule** to pick a mode:
  - **Every N hours** (`mode: "hourly"`) — fires every N hours from the last run
  - **Every N days at HH:MM** (`mode: "daily_at"`) — fires every N days at a specific clock time (e.g. every 3 days at 12:00)

  Click **Save schedule** and the change applies to the running scheduler immediately (via `request_wake()` — no need to wait for the next tick). The card shows the human-readable summary plus the next scheduled run time.

- **Generate & Post Now** — let the LLM pick today's hottest article, generate the post, attach the image, and publish.
- **Choose Article** — open a modal listing the top 20 ranked articles with thumbnails; click any one to write a post specifically about that story.
- **Post history** — every published post with content, image, timestamp, and a "View on LinkedIn" link.

**Important**: the scheduler runs *inside* this `uvicorn` process (started by `app.py`'s lifespan handler). It stops the moment you `Ctrl+C` the server. To run it 24/7, start `uvicorn` under a process supervisor like `systemd`, `launchctl`, or `pm2` — or inside `tmux`/`screen` so it survives logout. See [Scheduling](#scheduling-automated-posts) below.

#### JSON API

The dashboard talks to a small JSON API that you can also hit directly:

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/posts`    | Past published posts (newest first) |
| `GET`  | `/api/articles` | Top-20 ranked candidate articles right now |
| `POST` | `/api/trigger`  | Run the pipeline now. Body `{}` = LLM picks. Body `{"article_link": "..."}` = post about that article. |
| `GET`  | `/api/schedule` | Current schedule + computed `next_run_at` |
| `PUT`  | `/api/schedule` | Update schedule (`mode`, `interval_hours`, `interval_days`, `post_hour`, `post_minute`, `enabled`) |

> The CLI commands below still work and share the same SQLite schedule the UI writes to — useful for ad-hoc dry-runs, debugging, or headless servers without a browser.

### CLI Commands

#### Fetch & Rank Articles Only (No AI, No Posting)

Quick check to see what articles are trending:

```bash
python autoposter.py --fetch-only
```

Output (a `rich` table):
```
  #   Score  Source                 Title
--------------------------------------------------------------------------------
  1  0.5821  TechCabal              How African fintechs are racing to ...
  2  0.5488  Techpoint Africa       Nigerian startup closes Series A ...
  ...
```

#### Dry Run (Generate Post, Don't Publish)

Fetches articles, generates a post via DeepSeek R1, prints it to the console. Nothing is posted to LinkedIn.

```bash
python autoposter.py
```

This is the **default mode** — safe for testing. The picked article is marked as "seen" in the database so it won't be re-picked next run. (Unpicked articles are *not* marked, so they remain available for future runs.)

#### Publish to LinkedIn

```bash
python autoposter.py --post
```

Runs the full pipeline and publishes the generated post — with image attachment if one was extracted — to your LinkedIn profile.

#### Continuous Loop Mode

```bash
python autoposter.py --loop
```

Reads the schedule from SQLite (the same row the dashboard writes to) and fires posts on time. Useful for headless servers without a browser.

> ⚠️ **Do not run `--loop` while `uvicorn app:app` is also running on the same machine / DB.** Both would read the same schedule and double-post. Choose one or the other.

---

## Scheduling (Automated Posts)

The recommended way to schedule auto-posts is the **dashboard** ([Web UI Dashboard](#web-ui-dashboard-recommended)) — open the schedule card, pick a mode, click save. The scheduler (`scheduler.scheduler_loop`) runs inside the `uvicorn` process and fires posts on time.

The scheduler:
- Ticks at most every 60 seconds, but also wakes immediately when the UI saves a new schedule (`request_wake()`).
- Always records `last_run_at` — even when the run fails — so a flaky feed can't put the loop into a tight retry storm.
- Persists `next_run_at` so the dashboard shows the upcoming fire time without recomputing.

To run uvicorn 24/7 in the background, use one of the deployment patterns below.

### Option A: tmux / screen (quick + simple)

```bash
tmux new -s inposts
venv/bin/uvicorn app:app --host 127.0.0.1 --port 5050 --log-level info
# Detach with Ctrl+B then D — uvicorn keeps running.
# Reattach later with: tmux attach -t inposts
```

### Option B: nohup (background, no terminal)

```bash
nohup venv/bin/uvicorn app:app --host 127.0.0.1 --port 5050 --log-level info \
    > logs/uvicorn.log 2>&1 &
```

### Option C: System Cron (one-shot — bypasses the dashboard scheduler)

If you don't need the dashboard or the in-process scheduler, you can drive posts directly with cron. Edit your crontab:

```bash
crontab -e
```

Add a line to run daily at 11:00 AM (system timezone):

```cron
0 11 * * * cd /Users/seismicconsultinggroup/Documents/Projects/git_projects/inposts && ./venv/bin/python autoposter.py --post >> /tmp/autoposter.log 2>&1
```

**Breakdown:**
- `0 11 * * *` — at 11:00 AM every day
- `cd ...` — navigate to the project directory
- `./venv/bin/python` — use the virtual environment's Python
- `>> /tmp/autoposter.log 2>&1` — append all output (including errors) to a log file

To verify the cron job is set:
```bash
crontab -l
```

To check logs:
```bash
tail -50 /tmp/autoposter.log
```

### Option D: Headless `--loop` (No Web UI)

If you don't need the dashboard but do want hourly / daily_at scheduling, run the scheduler as a standalone CLI process:

```bash
nohup ./venv/bin/python autoposter.py --loop >> logs/autoposter.log 2>&1 &
```

This reads the same `schedule` row from SQLite. Edit the schedule via the dashboard once (anywhere — even on your laptop), then run the loop on the headless server.

> ⚠️ Don't run both `uvicorn app:app` and `autoposter.py --loop` on the same DB — they'd fire twice per slot.

---

## Customization

### Adding or Removing RSS Feeds

Edit the `RSS_FEEDS` list in `config.py`:

```python
RSS_FEEDS = [
    {
        "name": "Your Feed Name",
        "url": "https://example.com/rss",
        "has_comments": False,  # Set True only if the feed exposes slash:comments
    },
    # ... more feeds
]
```

The current default set is African tech publications (TechNext24, TechCabal, Techpoint Africa, Tech Economy). The commented-out Hacker News / TechCrunch / Wired / Ars Technica entries are kept in `config.py` as a reference if you want to switch.

### Changing the AI Prompt

The two prompts (LLM-picks-the-article and you-picked-the-article) live in `llm.generate_post`. Tweak tone, length, structure, or hashtag rules there.

### Using a Different Ollama Model

Set in `.env`:
```
OLLAMA_MODEL=llama3.1
```

Or any model you have pulled in Ollama (`ollama list` to see available models). `llm.py` always strips `<think>…</think>` tags, which is harmless for non-reasoning models.

### Adjusting the Hotness Window

By default, only articles from the last 24 hours are considered. Change in `.env`:
```
HOTNESS_WINDOW_HOURS=48
```

### Resetting the Seen Articles Database

If you want to re-process previously seen articles:
```bash
rm seen_articles.db
```

A fresh database (with the `seen_articles`, `posts`, and `schedule` tables) is created automatically on the next run by `db.init_db`.

> ⚠️ Deleting the DB also wipes your post history and your saved schedule. To clear *only* the seen-articles table:
> ```bash
> sqlite3 seen_articles.db "DELETE FROM seen_articles;"
> ```

---

## Troubleshooting

### "model 'deepseek-r1' not found"

Pull the model first:
```bash
ollama pull deepseek-r1
```

Check available models:
```bash
ollama list
```

If the model name differs (e.g., `deepseek-r1:7b`), update `.env`:
```
OLLAMA_MODEL=deepseek-r1:7b
```

### Ollama timeout

DeepSeek R1 can take 60–120 seconds to generate a response on commodity hardware. Increase the timeout in `.env`:
```
OLLAMA_TIMEOUT=300
```

`llm.py` uses `tenacity` to retry network-level failures up to 3 times with exponential backoff, but a hard timeout still surfaces as a final error.

### Post published but image is missing

Three likely causes, in order of frequency:

1. **Source image was hotlink-protected and the bypass failed.** `social._download_source_image` sends a Chrome UA + same-origin Referer to defeat most WordPress protections. If a site uses a stricter check (signed URLs, IP allowlisting), the download returns `None` and the post goes out text-only. Check `logs/autoposter.log` for `Could not download source image …`.
2. **LinkedIn never marked the asset as `AVAILABLE`.** `social._wait_for_image_available` polls for 15s; very large images or slow LinkedIn processing can exceed that. Look for `Image … not AVAILABLE after 15s` in the log.
3. **The article had no extractable image.** `rss._extract_image_url` checks `media:content`, `media:thumbnail`, `enclosure`, and inline `<img>` tags. If none yield a downloadable `http(s)` URL, the post is text-only — `Picked article has no image` will be in the log.

### LinkedIn 401 error (token expired)

Access tokens expire after 60 days. Repeat [Steps 4–5](#step-4-get-an-authorization-code) from the LinkedIn setup to get a new token, then update `LINKEDIN_ACCESS_TOKEN` in `.env`.

### LinkedIn 429 error (rate limited)

LinkedIn limits API calls. `social.py` logs the `Retry-After` header value. Wait that long before retrying.

### LinkedIn 403 error (author field)

Make sure your `LINKEDIN_AUTHOR_URN` uses the `urn:li:person:` format (not `urn:li:member:`). See [Step 6](#step-6-find-your-author-urn-person-id).

### No new articles found

This happens when all fetched articles have already been processed. Either:
- Wait for new articles to be published
- Increase the time window: `HOTNESS_WINDOW_HOURS=48`
- Reset the seen table (see [above](#resetting-the-seen-articles-database))

### SSL or network errors

Ensure you have internet connectivity. If behind a proxy:
```bash
export HTTPS_PROXY=http://your-proxy:port
```

### Scheduler doesn't fire

Check, in order:
1. The schedule is **enabled** — `GET /api/schedule` should show `"enabled": true`.
2. `uvicorn app:app` (or `autoposter.py --loop`) is actually running.
3. `logs/autoposter.log` shows `Scheduler started` near the top of the process's logs.
4. `next_run_at` is in the future — the dashboard card surfaces this.
