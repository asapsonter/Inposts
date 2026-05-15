# InPosts — LinkedIn Auto-Poster

An automated tool that fetches the hottest tech news daily, generates a professional LinkedIn post using a local AI model (Ollama + DeepSeek R1), and publishes it to your LinkedIn profile.

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

1. **Fetches** articles from 5 major tech RSS feeds (Hacker News, TechCrunch, The Verge, Wired, Ars Technica)
2. **Ranks** them by a hotness score combining recency (60%) and popularity/comments (40%)
3. **Deduplicates** using a local SQLite database so you never post about the same story twice
4. **Generates** a polished LinkedIn post using DeepSeek R1 running locally via Ollama (no cloud API costs)
5. **Publishes** directly to your LinkedIn profile via the official LinkedIn REST API

---

## How It Works

```
RSS Feeds (5 sources)
        |
        v
  feedparser --- parse articles from last 24 hours
        |
        v
  SQLite dedup --- skip already-seen article URLs
        |
        v
  Hotness scoring --- 60% recency + 40% popularity (comment count)
        |
        v
  Top 20 articles --- sent as context to DeepSeek R1 via Ollama
        |
        v
  Generated post --- printed to console for review
        |
        v
  (if --post) --- published via LinkedIn REST API (/rest/posts)
```

### Hotness Scoring

Each article gets a score from 0 to 1:

- **Recency (60% weight):** Linear decay over the configured time window (default 24h). A brand-new article scores 1.0, a 24h-old article scores 0.0.
- **Popularity (40% weight):** Based on comment count, normalized against the most-commented article in the batch. Feeds without comment data (TechCrunch, The Verge, etc.) get 0 here, so Hacker News articles with high engagement get a boost.

### AI Post Generation

The top-ranked articles are sent to DeepSeek R1 with a prompt that instructs it to:
- Pick the single most important story
- Write a professional LinkedIn post (max 300 words)
- Include a catchy hook, 2-3 insightful sentences, and 3 hashtags
- Output only the final post text (no reasoning or commentary)

DeepSeek R1's internal `<think>...</think>` reasoning tags are automatically stripped from the output.

---

## Project Structure

```
inposts/
├── autoposter.py       # Main script — fetch, rank, generate, publish
├── config.py           # All configuration — reads from .env
├── requirements.txt    # Python dependencies (requests, feedparser, python-dotenv)
├── .env.example        # Template for credentials — copy to .env
├── .env                # Your actual credentials (gitignored, never committed)
├── .gitignore          # Git ignore rules
├── seen_articles.db    # Auto-created SQLite database (gitignored)
├── venv/               # Python virtual environment (gitignored)
└── README.md           # This file
```

---

## Prerequisites

- **macOS** (Linux also works)
- **Python 3.10+**
- **Ollama** installed with DeepSeek R1 model pulled:
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
| `requests` | HTTP calls to Ollama and LinkedIn APIs |
| `feedparser` | RSS/Atom feed parsing |
| `python-dotenv` | Load credentials from `.env` file |

All other modules used (`sqlite3`, `json`, `re`, `argparse`, `time`, `logging`) are Python standard library.

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
# POST_HOUR=11
# POST_MINUTE=0
# MAX_ARTICLES=20
# HOTNESS_WINDOW_HOURS=24
```

All configuration lives in `config.py`, which reads from environment variables (via `.env`). You can also set these as regular shell environment variables if you prefer.

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
  - **Every N hours** — fires every N hours from the last run
  - **Every N days at HH:MM** — fires every N days at a specific clock time (e.g. every 3 days at 12:00)

  Click **Save schedule** and the change applies to the running scheduler immediately. The card shows the human-readable summary plus the next scheduled run time.

- **Generate & Post Now** — let the LLM pick today's hottest article and publish.
- **Choose Article** — open a modal listing the top 20 ranked articles with thumbnails, click any one to write a post specifically about that story.
- **Post history** — every published post with a "View on LinkedIn" link.

**Important**: the scheduler runs *inside* this `uvicorn` process. It stops the moment you `Ctrl+C` the server. To run it 24/7, start `uvicorn` under a process supervisor like `systemd`, `launchctl`, or `pm2` — or inside `tmux`/`screen` so it survives logout. See [Scheduling](#scheduling-automated-posts) below.

> The CLI commands in the next section still work and share the same SQLite schedule the UI writes to — they're useful for ad-hoc dry-runs, debugging, or headless servers without a browser.

### CLI Commands

#### Fetch & Rank Articles Only (No AI, No Posting)

Quick check to see what articles are trending:

```bash
python autoposter.py --fetch-only
```

Output:
```
  #   Score  Source                 Title
--------------------------------------------------------------------------------
  1  0.5821  The Verge              A folk musician became a target for AI fakes...
  2  0.5488  TechCrunch             Anthropic says Claude Code subscribers will...
  ...
```

#### Dry Run (Generate Post, Don't Publish)

Fetches articles, generates a post via DeepSeek R1, and prints it to the console. Nothing is posted to LinkedIn.

```bash
python autoposter.py
```

This is the **default mode** — safe for testing. Articles are still marked as "seen" in the database to avoid regenerating the same content.

#### Publish to LinkedIn

```bash
python autoposter.py --post
```

Runs the full pipeline and publishes the generated post to your LinkedIn profile.

#### Continuous Loop Mode

```bash
python autoposter.py --loop
```

Reads the schedule from SQLite (the same row the dashboard writes to) and fires posts on time. Useful for headless servers without a browser.

> ⚠️ **Do not run `--loop` while `uvicorn app:app` is also running.** Both processes would read the same schedule and double-post. Choose one or the other.

---

## Scheduling (Automated Posts)

The recommended way to schedule auto-posts is the **dashboard** ([Web UI Dashboard](#web-ui-dashboard-recommended)) — open the schedule card, pick a mode, click save. The scheduler runs inside the `uvicorn` process and fires posts on time.

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

### Option C: System Cron

Edit your crontab:

```bash
crontab -e
```

Add a line to run daily at 11:00 AM (WAT / Nigerian time). Adjust for your system's timezone:

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

If you don't need the dashboard, run the scheduler as a standalone CLI process:

```bash
nohup ./venv/bin/python autoposter.py --loop >> logs/autoposter.log 2>&1 &
```

This reads the same schedule from SQLite. Edit the schedule via the dashboard once (anywhere, even on your laptop), then run the loop on the headless server.

> ⚠️ Don't run both `uvicorn app:app` and `autoposter.py --loop` on the same server / same database — they'd fire twice per slot.

---

## Customization

### Adding or Removing RSS Feeds

Edit the `RSS_FEEDS` list in `config.py`:

```python
RSS_FEEDS = [
    {
        "name": "Your Feed Name",
        "url": "https://example.com/rss",
        "has_comments": False,  # Set True if the feed includes comment counts
    },
    # ... more feeds
]
```

### Changing the AI Prompt

The prompt sent to DeepSeek R1 is in the `generate_post()` function in `autoposter.py`. You can customize the tone, length, style, or structure of the generated posts by editing the prompt string.

### Using a Different Ollama Model

Set in `.env`:
```
OLLAMA_MODEL=llama3.1
```

Or any model you have pulled in Ollama (`ollama list` to see available models).

### Adjusting the Hotness Window

By default, only articles from the last 24 hours are considered. Change this in `.env`:
```
HOTNESS_WINDOW_HOURS=48
```

### Resetting the Seen Articles Database

If you want to re-process previously seen articles:
```bash
rm seen_articles.db
```

A fresh database is created automatically on the next run.

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

DeepSeek R1 can take 60-120 seconds to generate a response. Increase the timeout in `.env`:
```
OLLAMA_TIMEOUT=300
```

### LinkedIn 401 error (token expired)

Access tokens expire after 60 days. Repeat [Steps 4-5](#step-4-get-an-authorization-code) from the LinkedIn setup to get a new token.

### LinkedIn 429 error (rate limited)

LinkedIn limits API calls. The script logs the `Retry-After` header value. Wait the indicated time before retrying.

### LinkedIn 403 error (author field)

Make sure your `LINKEDIN_AUTHOR_URN` uses the `urn:li:person:` format (not `urn:li:member:`). See [Step 6](#step-6-find-your-author-urn-person-id) for how to find the correct URN.

### No new articles found

This happens when all fetched articles have already been processed. Either:
- Wait for new articles to be published
- Increase the time window: `HOTNESS_WINDOW_HOURS=48`
- Reset the database: `rm seen_articles.db`

### SSL or network errors

Ensure you have internet connectivity. If behind a proxy:
```bash
export HTTPS_PROXY=http://your-proxy:port
```
