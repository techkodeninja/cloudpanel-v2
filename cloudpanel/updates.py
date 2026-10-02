"""Knowing when a newer version is out.

A new version is a GitHub release (tag v0.0.1, ...) with the bundled
program attached as `cloudpanel`. Merging to develop alone doesn't reach
servers; publishing a release does.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__, ui
from .errors import CloudPanelError

REPO = "techkodeninja/cloudpanel-v2"
LATEST_RELEASE = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_FILE = f"https://github.com/{REPO}/releases/download/{{tag}}/cloudpanel"
BRANCH_FILE = f"https://raw.githubusercontent.com/{REPO}/{{branch}}/dist/cloudpanel"
STATE_FILE = Path("/root/.cloudpanel/update-check")
CHECK_EVERY = 24 * 60 * 60  # seconds


def fetch(url, timeout=60):
    headers = {"User-Agent": "cloudpanel"}
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404 and url == LATEST_RELEASE:
            raise CloudPanelError(f"No release published yet on github.com/{REPO}/releases.") from None
        raise CloudPanelError(f"Could not download {url}: {error}") from None
    except OSError as error:
        raise CloudPanelError(f"Could not download {url}: {error}") from None


def latest_release(timeout=10):
    """The newest published release: (version, tag), e.g. ('0.0.1', 'v0.0.1')."""
    try:
        tag = json.loads(fetch(LATEST_RELEASE, timeout))["tag_name"]
    except (ValueError, KeyError, TypeError):
        raise CloudPanelError("Could not read the latest release from GitHub.") from None
    return tag.lstrip("v"), tag


def latest_version(timeout=10):
    return latest_release(timeout)[0]


def parse(version):
    """'0.0.10' -> (0, 0, 10), so 0.0.10 counts as newer than 0.0.9."""
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.\-]", version))


def is_newer(candidate, current=__version__):
    return parse(candidate) > parse(current)


def notify():
    """At most once a day, after a command, say if an update is out.

    Only when someone is watching (a terminal), with a short timeout, and
    silent if anything goes wrong: it must never get in the way. Turn it off
    with CLOUDPANEL_NO_UPDATE_CHECK=1.
    """
    if not sys.stdout.isatty() or os.environ.get("CLOUDPANEL_NO_UPDATE_CHECK"):
        return
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        state = {}
    try:
        if time.time() - state.get("checked", 0) > CHECK_EVERY:
            state = {"checked": time.time(), "latest": latest_version(timeout=3)}
            STATE_FILE.parent.mkdir(mode=0o700, exist_ok=True)
            STATE_FILE.write_text(json.dumps(state))
        latest = state.get("latest")
        if latest and is_newer(latest):
            print()
            ui.info(f"cloudpanel {latest} is available (you have {__version__}). Update: {ui.bold('cloudpanel self:update')}")
    except Exception:
        pass
