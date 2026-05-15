import logging
import sys
import time
from urllib.parse import quote

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import config

log = logging.getLogger("autoposter.social")

LINKEDIN_API_VERSION = "202604"
# How long to wait for LinkedIn to finish ingesting an uploaded image
# before giving up and posting text-only. LinkedIn's image upload is
# async — referencing the URN before it reaches AVAILABLE causes the
# post to publish without the media attachment (no error, just dropped).
IMAGE_READY_TIMEOUT_S = 15
IMAGE_READY_POLL_INTERVAL_S = 0.5


def _wait_for_image_available(image_urn: str, token: str) -> bool:
    """Poll GET /rest/images/{urn} until status == 'AVAILABLE' or timeout.
    Returns True if the image is ready to be referenced in a post."""
    encoded = quote(image_urn, safe="")
    url = f"https://api.linkedin.com/rest/images/{encoded}"
    headers = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": LINKEDIN_API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    deadline = time.monotonic() + IMAGE_READY_TIMEOUT_S
    last_status = None
    while time.monotonic() < deadline:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                last_status = r.json().get("status")
                if last_status == "AVAILABLE":
                    log.info("Image %s ready (AVAILABLE).", image_urn)
                    return True
                if last_status in ("FAILED", "ERROR"):
                    log.warning(
                        "LinkedIn image processing failed (status=%s): %s",
                        last_status, r.text[:300],
                    )
                    return False
            else:
                log.debug(
                    "Image status check returned %d: %s",
                    r.status_code, r.text[:200],
                )
        except requests.RequestException as e:
            log.debug("Image status poll error (will retry): %s", e)
        time.sleep(IMAGE_READY_POLL_INTERVAL_S)

    log.warning(
        "Image %s not AVAILABLE after %ds (last status=%s) — posting text-only.",
        image_urn, IMAGE_READY_TIMEOUT_S, last_status,
    )
    return False


def _upload_image_to_linkedin(image_url: str) -> str | None:
    """Download a remote image and register it with LinkedIn.
    Returns the image URN, or None on failure."""
    token = config.linkedin_access_token
    author = config.linkedin_author_urn

    # 1. Fetch source image bytes
    try:
        img_resp = requests.get(image_url, timeout=30)
        img_resp.raise_for_status()
        image_bytes = img_resp.content
    except requests.RequestException as e:
        log.warning("Could not download source image %s: %s", image_url, e)
        return None

    init_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "LinkedIn-Version": LINKEDIN_API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }

    # 2. Ask LinkedIn for an upload URL + image URN
    try:
        init_resp = requests.post(
            "https://api.linkedin.com/rest/images?action=initializeUpload",
            headers=init_headers,
            json={"initializeUploadRequest": {"owner": author}},
            timeout=30,
        )
        init_resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("LinkedIn initializeUpload failed: %s", e)
        return None

    value = init_resp.json().get("value", {})
    upload_url = value.get("uploadUrl")
    image_urn = value.get("image")
    if not upload_url or not image_urn:
        log.warning(
            "initializeUpload response missing uploadUrl/image: %s",
            init_resp.text[:300],
        )
        return None

    # 3. PUT raw bytes to the returned upload URL
    try:
        put_resp = requests.put(
            upload_url,
            headers={"Authorization": f"Bearer {token}"},
            data=image_bytes,
            timeout=60,
        )
        put_resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("LinkedIn image byte upload failed: %s", e)
        return None

    log.info("Image uploaded to LinkedIn (URN: %s)", image_urn)
    return image_urn


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type(requests.RequestException))
def publish_to_linkedin(
    post_text: str,
    image_url: str | None = None,
    image_alt: str = "Article image",
):
    """Publish a post to LinkedIn using the REST Posts API (/rest/posts).
    If image_url is provided, the image is uploaded and attached."""
    token = config.linkedin_access_token
    author = config.linkedin_author_urn

    if not token or not author:
        log.error(
            "LinkedIn credentials missing. Set LINKEDIN_ACCESS_TOKEN and "
            "LINKEDIN_AUTHOR_URN in your .env file."
        )
        sys.exit(1)

    # Upload image first (if provided) so we have a URN to embed in the post.
    # Then wait for LinkedIn to finish ingesting it — referencing a still-
    # processing URN causes the media to be silently dropped from the post.
    image_urn = _upload_image_to_linkedin(image_url) if image_url else None
    if image_urn and not _wait_for_image_available(image_urn, token):
        log.warning(
            "Skipping image attachment because asset never became AVAILABLE."
        )
        image_urn = None

    payload = {
        "author": author,
        "commentary": post_text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    if image_urn:
        payload["content"] = {"media": {"id": image_urn, "altText": image_alt[:300]}}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "LinkedIn-Version": LINKEDIN_API_VERSION,
        # Required for the REST Posts API to correctly parse the
        # `content.media` field. Without this header LinkedIn falls back to
        # an older schema and silently drops the image attachment — the
        # post still publishes (201) but with no media.
        "X-Restli-Protocol-Version": "2.0.0",
    }

    log.info(
        "Publishing to LinkedIn (with image=%s) …",
        "yes" if image_urn else "no",
    )
    try:
        resp = requests.post(
            "https://api.linkedin.com/rest/posts",
            headers=headers,
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        log.error("LinkedIn request failed: %s", e)
        raise

    if resp.status_code == 201:
        post_id = resp.headers.get("x-linkedin-id", "unknown")
        log.info("Published successfully! Post ID: %s", post_id)
        return post_id

    # Handle common errors
    if resp.status_code == 401:
        log.error(
            "LinkedIn token expired or invalid (401). "
            "Generate a new access token and update your .env file."
        )
    elif resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "unknown")
        log.error("LinkedIn rate limit hit (429). Retry after %s seconds.", retry_after)
    else:
        log.error(
            "LinkedIn API error %d: %s", resp.status_code, resp.text[:500]
        )

    resp.raise_for_status()
