"""Knowing when a newer version is on GitHub.

The version number lives in cloudpanel/__init__.py, so the latest one is
read from that small file on GitHub (no download of the whole program).
"""

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

from . import __version__, ui
from .errors import CloudPanelError

RAW = "https://raw.githubusercontent.com/techkodeninja/cloudpanel-v2/{branch}/{path}"
STATE_FILE = Path("/root/.cloudpanel/update-check")
CHECK_EVERY = 24 * 60 * 60  # seconds


def fetch(branch, path, timeout=60):
    url = RAW.format(branch=branch, path=path)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except OSError as error:
        raise CloudPanelError(f"Could not download {url}: {error}") from None


def latest_version(branch="main", timeout=10):
    """The version number on GitHub, e.g. '2.0.1'."""
    text = fetch(branch, "cloudpanel/__init__.py", timeout).decode("utf-8", "replace")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise CloudPanelError("Could not find the version number on GitHub.")
    return match.group(1)


def parse(version):
    """'2.0.10' -> (2, 0, 10), so 2.0.10 counts as newer than 2.0.9."""
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
