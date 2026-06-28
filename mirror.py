"""Mirror data/forecast.csv to GitHub via the contents API.

Keeps the existing raw-GitHub URL (the one Excel pulls from) alive when the
real collector runs locally. No git binary or checkout needed — just a token.

Configured entirely by environment variables; if they're unset, mirroring is
skipped so the stack still runs fully local.

    GITHUB_TOKEN   fine-grained PAT with Contents: read & write on the repo
    GITHUB_REPO    "owner/name", e.g. alivebe-a11y/weat
    GITHUB_BRANCH  branch to write to (default: main)
"""

import base64
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

import config

log = logging.getLogger("mirror")

API = "https://api.github.com"


def push_csv() -> bool:
    """Push forecast.csv if it differs from the copy on GitHub. Returns True
    if a commit was made, False if skipped or unchanged."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO")
    branch = os.environ.get("GITHUB_BRANCH", "main")
    if not token or not repo:
        log.info("Mirror skipped (GITHUB_TOKEN / GITHUB_REPO not set)")
        return False

    path = config.OUTPUT_FILE  # same relative path in the repo
    local = Path(path)
    if not local.exists():
        log.warning("Mirror skipped: %s does not exist", path)
        return False

    url = f"{API}/repos/{repo}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    content = local.read_bytes()
    b64 = base64.b64encode(content).decode()

    get = requests.get(url, headers=headers, params={"ref": branch}, timeout=30)
    sha = None
    if get.status_code == 200:
        body = get.json()
        sha = body.get("sha")
        remote_b64 = body.get("content", "").replace("\n", "")
        if remote_b64 == b64:
            log.info("Mirror: GitHub already up to date")
            return False
    elif get.status_code != 404:
        get.raise_for_status()

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    payload = {
        "message": f"forecast: mirror {stamp} [skip ci]",
        "content": b64,
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    put = requests.put(url, headers=headers, json=payload, timeout=30)
    put.raise_for_status()
    log.info("Mirror: pushed forecast.csv to %s@%s", repo, branch)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    push_csv()
