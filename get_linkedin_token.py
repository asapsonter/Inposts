#!/usr/bin/env python3
"""
Refresh the LinkedIn access token without any manual copy-paste.

LinkedIn member tokens (w_member_social) expire ~60 days. When posting starts
returning 401, run this script:

    python get_linkedin_token.py

It opens your browser to LinkedIn's consent screen, catches the redirect on a
temporary local server, exchanges the authorization code for an access token
(no 30-second race, no malformed-curl pain), and rewrites LINKEDIN_ACCESS_TOKEN
in your .env. Restart the backend afterwards to pick up the new token.

Reads LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET from .env (or prompts if
missing). The redirect URL below must be registered on your LinkedIn app's
Auth tab under "Authorized redirect URLs".
"""

import http.server
import re
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests

REDIRECT_URI = "http://localhost:8080/callback"
SCOPE = "w_member_social"  # the only scope needed to post as a member
ENV_PATH = Path(__file__).parent / ".env"

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


def read_env() -> dict[str, str]:
    """Parse .env into a dict (ignores comments/blank lines)."""
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    return env


def write_token(token: str) -> None:
    """Replace (or append) LINKEDIN_ACCESS_TOKEN in .env, leaving the rest intact."""
    text = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    new_line = f"LINKEDIN_ACCESS_TOKEN={token}"
    if re.search(r"^LINKEDIN_ACCESS_TOKEN=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^LINKEDIN_ACCESS_TOKEN=.*$", new_line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + "\n" + new_line + "\n"
    ENV_PATH.write_text(text)


# Captured by the callback handler and read by the main thread.
_auth_result: dict[str, str] = {}


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (stdlib naming)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _auth_result["code"] = params.get("code", [None])[0]
        _auth_result["error"] = params.get("error_description", params.get("error", [None]))[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if _auth_result.get("code"):
            msg = "Authorization received. You can close this tab and return to the terminal."
        else:
            msg = f"Authorization failed: {_auth_result.get('error')}. Check the terminal."
        self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode())

    def log_message(self, *_args):  # silence default request logging
        pass


def main() -> int:
    env = read_env()
    client_id = env.get("LINKEDIN_CLIENT_ID") or input("LinkedIn Client ID: ").strip()
    client_secret = env.get("LINKEDIN_CLIENT_SECRET") or input("LinkedIn Client Secret: ").strip()
    if not client_id or not client_secret:
        print("Missing client ID/secret. Set them in .env or enter when prompted.")
        return 1

    auth_link = (
        f"{AUTH_URL}?response_type=code"
        f"&client_id={urllib.parse.quote(client_id)}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
        f"&scope={urllib.parse.quote(SCOPE)}"
    )

    # Start the one-shot callback server before opening the browser.
    server = http.server.HTTPServer(("localhost", 8080), _CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("\nOpening LinkedIn authorization in your browser…")
    print("If it doesn't open, paste this URL manually:\n")
    print(auth_link, "\n")
    webbrowser.open(auth_link)
    print("Waiting for you to click 'Allow'…")

    # Block until the callback handler captures a result.
    while "code" not in _auth_result and "error" not in _auth_result:
        pass
    server.shutdown()

    if not _auth_result.get("code"):
        print(f"\nAuthorization failed: {_auth_result.get('error')}")
        print("If this mentions a scope, add the 'Share on LinkedIn' product to your app.")
        return 1

    print("Got authorization code — exchanging for an access token…")
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _auth_result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"\nToken exchange failed ({resp.status_code}): {resp.text}")
        return 1

    data = resp.json()
    token = data.get("access_token")
    if not token:
        print(f"\nNo access_token in response: {data}")
        return 1

    write_token(token)
    days = data.get("expires_in", 0) // 86400
    print(f"\n✅ New access token saved to .env (valid ~{days} days).")
    print("Restart the backend so it picks up the new token.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
