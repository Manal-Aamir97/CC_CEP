"""Minimal read-only GitHub access, standard library only.

A token is optional. Without one, GitHub allows 60 API requests per hour, which is enough for
the case set. To raise the limit to 5,000, set GITHUB_TOKEN yourself in your own shell. The token
is sent only to api.github.com and is never printed or written to disk.
"""
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import PurePosixPath

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
USER_AGENT = "cep-llm-config-dataset/1.0"
MAX_ATTEMPTS = 4
RETRY_WAIT_S = 2
REPO_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class NotFound(RuntimeError):
    """The repository, commit or file no longer exists (or is private)."""


class RateLimited(RuntimeError):
    """GitHub's hourly API limit is used up; the message says when it resets."""


def check_repo_name(repo):
    if not REPO_NAME.fullmatch(repo) or ".." in repo:
        raise ValueError(f"not a GitHub owner/name: {repo!r}")
    return repo


def safe_relative_path(path):
    """A repository file path that cannot escape the folder it is saved into."""
    pure = PurePosixPath(path)
    if pure.is_absolute() or not pure.parts or any(part in ("..", "") for part in pure.parts) \
            or ":" in pure.parts[0] or "\\" in path or "//" in path:
        raise ValueError(f"unsafe path in repository listing: {path!r}")
    return pure


def _request(url, api=False):
    headers = {"User-Agent": USER_AGENT}
    if api:
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 451):
                raise NotFound(url) from None
            if exc.code in (403, 429) and exc.headers.get("X-RateLimit-Remaining") == "0":
                reset = int(exc.headers.get("X-RateLimit-Reset", "0") or 0)
                raise RateLimited(f"GitHub API limit reached; it resets at "
                                  f"{time.strftime('%H:%M', time.localtime(reset))}") from None
            if exc.code < 500 or attempt == MAX_ATTEMPTS:
                raise
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError):
            if attempt == MAX_ATTEMPTS:   # a dropped connection is worth another try
                raise
        time.sleep(RETRY_WAIT_S * attempt)


def get_json(path, **query):
    """GET api.github.com<path>?<query> and decode the JSON."""
    url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
    return json.loads(_request(url, api=True))


def get_raw(repo, ref, path):
    """Bytes of one file at one commit (raw.githubusercontent.com; no API limit, no token)."""
    check_repo_name(repo)
    quoted = urllib.parse.quote(str(safe_relative_path(path)))
    return _request(f"{RAW}/{repo}/{ref}/{quoted}")
