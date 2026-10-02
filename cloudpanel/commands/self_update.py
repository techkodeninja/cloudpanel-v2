"""self:update — replace this program with the latest version from GitHub.

    cloudpanel self:update            install the latest release if it's newer
    cloudpanel self:update --check    only say whether there is one
    cloudpanel self:update --force    reinstall the latest release anyway
    cloudpanel self:update --branch=x try an unreleased branch (for testing)
"""

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .. import __version__, ui, updates
from ..errors import CloudPanelError


def installed_path():
    """The bundled file being run, or None when running from source."""
    path = Path(sys.argv[0]).resolve()
    return path if path.is_file() and zipfile.is_zipfile(path) else None


def self_update(params, target=None):
    branch = params.get("branch") if isinstance(params.get("branch"), str) else None

    if branch:
        url, source = updates.BRANCH_FILE.format(branch=branch), f"branch {branch}"
    else:
        latest, tag = updates.latest_release()
        if params.get("check") or not params.get("force"):
            if not updates.is_newer(latest):
                ui.success(f"Up to date (cloudpanel {__version__}).")
                return
            if params.get("check"):
                ui.info(f"cloudpanel {ui.bold(latest)} is available (you have {__version__}). Update: cloudpanel self:update")
                return
        url, source = updates.RELEASE_FILE.format(tag=tag), f"release {tag}"

    target = target or installed_path()
    if target is None:
        raise CloudPanelError("Running from source; update with git pull instead.")

    data = updates.fetch(url)

    # Write next to the target, check it runs, then swap it in one step.
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".cloudpanel-")
    tmp = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.chmod(tmp, 0o755)
        if not data.startswith(b"#!") or not zipfile.is_zipfile(tmp):
            raise CloudPanelError(f"The download from {source} is not a cloudpanel bundle.")
        check = subprocess.run([sys.executable, str(tmp), "--version"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=60)
        if check.returncode != 0:
            raise CloudPanelError(f"The downloaded version doesn't run: {check.stderr.strip() or check.stdout.strip()}")
        new_version = check.stdout.strip().removeprefix("cloudpanel ").strip()
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)

    ui.success(f"Updated {ui.bold(target)}: {__version__} -> {ui.bold(new_version)} ({source}).")
