"""self:update — replace this program with the latest version from GitHub.

    cloudpanel self:update [--branch=main]
"""

import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .. import __version__, ui
from ..errors import CloudPanelError

URL = "https://raw.githubusercontent.com/techkodeninja/cloudpanel-v2/{branch}/dist/cloudpanel"


def installed_path():
    """The bundled file being run, or None when running from source."""
    path = Path(sys.argv[0]).resolve()
    return path if path.is_file() and zipfile.is_zipfile(path) else None


def self_update(params, target=None):
    branch = params.get("branch") if isinstance(params.get("branch"), str) else "main"
    target = target or installed_path()
    if target is None:
        raise CloudPanelError("Running from source; update with git pull instead.")

    url = URL.format(branch=branch)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
    except OSError as error:
        raise CloudPanelError(f"Could not download {url}: {error}") from None

    # Write next to the target, check it runs, then swap it in one step.
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".cloudpanel-")
    tmp = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.chmod(tmp, 0o755)
        if not data.startswith(b"#!") or not zipfile.is_zipfile(tmp):
            raise CloudPanelError(f"{url} is not a cloudpanel bundle.")
        check = subprocess.run([sys.executable, str(tmp), "--version"], capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=60)
        if check.returncode != 0:
            raise CloudPanelError(f"The downloaded version doesn't run: {check.stderr.strip() or check.stdout.strip()}")
        new_version = check.stdout.strip().removeprefix("cloudpanel ").strip()
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)

    ui.success(f"Updated {ui.bold(target)}: {__version__} -> {ui.bold(new_version)} ({branch}).")
