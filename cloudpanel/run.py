"""Running external commands safely.

Every command runs WITHOUT a shell, with its arguments as a list, so values
such as passwords and URLs are passed exactly as given ($, &, !, =, spaces
and quotes included) and can never be run as shell code.
"""

import os
import shutil
import subprocess

from .errors import CommandError


def run(cmd, *, show=False, cwd=None, input=None):
    """Run `cmd` (a list) and return its output.

    show=False (default): output is captured and returned.
    show=True: output goes straight to the terminal (long installs).
    Raises CommandError, including what the command printed, if it can't
    start or exits non-zero.
    """
    if isinstance(cmd, str):
        raise TypeError("run() takes a list of arguments, not a string")

    capture = None if show else subprocess.PIPE
    # A captured command never gets the keyboard: if a tool unexpectedly
    # asks a question it gets end-of-input instead of freezing the run.
    stdin = None if (show or input is not None) else subprocess.DEVNULL
    try:
        result = subprocess.run(
            [str(part) for part in cmd],
            cwd=cwd,
            input=input,
            stdin=stdin if input is None else None,
            text=True,
            stdout=capture,
            stderr=capture,
        )
    except FileNotFoundError:
        raise CommandError(f"{cmd[0]} is not installed or not on PATH") from None

    if result.returncode != 0:
        output = f"{result.stderr or ''}{result.stdout or ''}".strip()
        label = " ".join(str(part) for part in cmd[:3])
        detail = f": {output}" if output else f" (exit code {result.returncode})"
        raise CommandError(f"{label} failed{detail}")

    return result.stdout or ""


def clpctl(args, **options):
    """Run a CloudPanel command: clpctl <args>."""
    return run(["clpctl", *args], **options)


def as_site_user(user, args, *, cwd=None, **options):
    """Run a command as a site user, with that user's HOME.

    Unless a cwd is given it runs from the user's home folder: the caller's
    folder (usually /root) is unreadable for the site user, and tools like
    git refuse to start there.
    """
    home = f"/home/{user}"
    if cwd is None and os.path.isdir(home):
        cwd = home
    return run(["sudo", "-u", user, "-H", *args], cwd=cwd, **options)


def command_exists(name):
    return shutil.which(name) is not None
