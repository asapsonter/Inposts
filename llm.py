import logging
import re
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import config

log = logging.getLogger("autoposter.llm")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_INDEX_RE = re.compile(r"INDEX:\s*(\d+)", re.IGNORECASE)


def _call_ollama(prompt: str) -> str:
    """Send a prompt to Ollama and return the cleaned response text."""
    log.info("Calling Ollama (%s) …", config.ollama_model)
    try:
        resp = requests.post(
            config.ollama_url,
            json={
                "model": config.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": 1024},
            },
            timeout=config.ollama_timeout,
        )
        resp.raise_for_status()
    except requests.Timeout:
        log.error("Ollama request timed out after %ds", config.ollama_timeout)
        raise
    except requests.RequestException as e:
        log.error("Ollama request failed: %s", e)
        raise

    raw_text = resp.json().get("response", "")
    # DeepSeek R1 wraps its internal reasoning in <think>…</think> tags — strip them
    return _THINK_RE.sub("", raw_text).strip()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
)
def generate_post(
    articles: list[dict],
    selected_index: int | None = None,
) -> tuple[str, int]:
    """Generate a LinkedIn post.

    If `selected_index` is given, write about that specific article.
    Otherwise let the LLM pick from the top-ranked list and return which
    one it picked. Returns (post_text, picked_index).
    """
    if not articles:
        raise ValueError("generate_post called with no articles")

    pool = articles[: config.max_articles]

    # --- Path A: user already picked an article ---
    if selected_index is not None:
        if not (0 <= selected_index < len(pool)):
            raise IndexError(
                f"selected_index {selected_index} out of range "
                f"(have {len(pool)} articles)"
            )
        a = pool[selected_index]
        prompt = f"""You are a professional LinkedIn content creator who writes about technology trends.

Write a LinkedIn post about this specific article:

Source: {a['source']}
Title: {a['title']}
Summary: {a['summary']}
URL: {a['link']}

Requirements:
- A catchy, attention-grabbing first line (hook).
- 2–3 insightful sentences explaining why this matters.
- 3 relevant hashtags at the end.
- Max 300 words.

Output ONLY the final post text. No preamble, no commentary, no labels."""
        body = _call_ollama(prompt)
        return body, selected_index

    # --- Path B: LLM picks from the top list ---
    digest_lines = []
    for i, a in enumerate(pool, 1):
        digest_lines.append(
            f"{i}. [{a['source']}] {a['title']}  "
            f"(score {a['score']}, comments {a['comment_count']})\n"
            f"   {a['summary'][:200]}\n   URL: {a['link']}"
        )
    digest = "\n".join(digest_lines)

    prompt = f"""You are a professional LinkedIn content creator who writes about technology trends.

Below is a ranked list of today's hottest tech news articles:

{digest}

Your task:
1. Pick the single most important / interesting story from the list above.
2. Write a professional, engaging LinkedIn post about it (max 300 words).
3. Structure: catchy hook, 2–3 insightful sentences, 3 relevant hashtags.

OUTPUT FORMAT (STRICT — follow exactly):
INDEX: <the number of the article you picked, 1 to {len(pool)}>
---
<the final post text, nothing else>

Do not include any preamble, labels, commentary, or explanation outside this format."""

    cleaned = _call_ollama(prompt)

    # Parse the INDEX line
    picked_index = 0
    m = _INDEX_RE.search(cleaned)
    if m:
        try:
            picked_index = int(m.group(1)) - 1  # convert 1-based to 0-based
            picked_index = max(0, min(picked_index, len(pool) - 1))
        except ValueError:
            picked_index = 0
    else:
        log.warning("LLM did not return INDEX: line — defaulting to top article")

    # Strip the INDEX line + separator from the body
    body = re.sub(r"^.*?---\s*\n", "", cleaned, count=1, flags=re.DOTALL).strip()
    if not body:
        # Separator not found — fall back to stripping just the INDEX line
        body = re.sub(r"^\s*INDEX:.*\n?", "", cleaned, count=1, flags=re.IGNORECASE).strip()

    return body, picked_index
