"""Numbered steps for long, multi-step commands (init:install).

    steps = Steps(total=6, log_path=Path("/root/.cloudpanel/install-....log"))
    with steps.step("Installing Certbot") as step:
        step.run(["apt-get", "install", "-y", "certbot"])
        step.detail = "already installed"

On a terminal each step shows a spinner and a timer while it runs, then one
line when it's done:

      1/6  Installing Certbot             already installed       0.1s ✓

Commands run in a step write their output to the log file, not the screen.
Messages the code prints during a step (warnings, notes) are shown under the
step's line; ✔ success messages only go to the log. Without a terminal
(pipes, scripts) there's no spinner, just the finished lines.
"""

import collections
import io
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import redirect_stderr, redirect_stdout

from . import ui
from .errors import CommandError

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
TAIL_LINES = 15


def plain(text):
    return ANSI.sub("", text)


def elapsed(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes}m{seconds:02d}s"


class Steps:
    def __init__(self, total, log_path):
        self.total = total
        self.number = 0
        self.log_path = log_path
        self.live = sys.stdout.isatty()  # spinner only on a terminal
        log_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(log_path.parent, 0o700)
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self._log = os.fdopen(descriptor, "w", buffering=1)

    def log(self, text):
        self._log.write(plain(text))
        if not text.endswith("\n"):
            self._log.write("\n")

    def step(self, label, *, long=False):
        self.number += 1
        return Step(self, self.number, label, long)

    def close(self):
        self._log.close()


class Step:
    def __init__(self, steps, number, label, long):
        self.steps = steps
        self.label = label
        self.counter = f"{number}/{steps.total}"
        self.long = long
        self.detail = ""
        self._latest = ""  # last line a command printed, shown next to the spinner
        self._messages = io.StringIO()

    # -- running commands -------------------------------------------------

    def run(self, cmd, *, cwd=None):
        """Run cmd (a list, no shell), its output going to the log.

        Raises CommandError with the last lines of output if it fails."""
        argv = [str(part) for part in cmd]
        self.steps.log(f"$ {' '.join(argv)}")
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        tail = collections.deque(maxlen=TAIL_LINES)
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, errors="replace")
        except FileNotFoundError:
            raise CommandError(f"{argv[0]} is not installed or not on PATH") from None
        for line in process.stdout:
            self.steps.log(line)
            line = plain(line).strip()
            if line:
                tail.append(line)
                self._latest = line
        code = process.wait()
        if code != 0:
            output = "\n".join(tail)
            label = " ".join(argv[:3])
            raise CommandError(f"{label} failed (exit code {code})" + (f":\n{output}" if output else ""))

    # -- showing the step --------------------------------------------------

    def __enter__(self):
        self._start = time.monotonic()
        self.steps.log(f"\n== {self.counter} {self.label} ==")
        self._out = sys.stdout
        if self.steps.live:
            self._stop = threading.Event()
            self._spinner = threading.Thread(target=self._spin, daemon=True)
            self._spinner.start()
        elif self.long:
            self._out.write(f"  {self.counter}  {self.label} …\n")
            self._out.flush()
        self._redirects = [redirect_stdout(self._messages), redirect_stderr(self._messages)]
        for redirect in self._redirects:
            redirect.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        for redirect in reversed(self._redirects):
            redirect.__exit__(None, None, None)
        if self.steps.live:
            self._stop.set()
            self._spinner.join()
            self._out.write("\r\033[K")

        messages = self._messages.getvalue()
        if messages.strip():
            self.steps.log(messages)
        if exc is not None:
            self.steps.log(f"FAILED: {exc}")
            mark = ui.red("✗")
        elif "⚠" in messages or "✖" in messages:
            mark = ui.yellow("⚠")
        else:
            mark = ui.green("✓")

        took = elapsed(time.monotonic() - self._start)
        self._out.write(self._line(took, mark) + "\n")
        # Warnings, notes and errors stay visible; successes are in the log.
        for line in messages.splitlines():
            if line.strip() and not plain(line).lstrip().startswith("✔"):
                self._out.write(f"         {line}\n")
        self._out.flush()
        return False

    def _line(self, took, mark):
        width = shutil.get_terminal_size((100, 20)).columns
        label = self.label.ljust(34)
        detail = self.detail[: max(10, width - 60)].ljust(26)
        return f"  {ui.gray(self.counter.rjust(5))}  {ui.bold(label)} {ui.gray(detail)} {took.rjust(7)} {mark}"

    def _spin(self):
        frame = 0
        while not self._stop.wait(0.1):
            width = shutil.get_terminal_size((100, 20)).columns
            took = elapsed(time.monotonic() - self._start)
            head = f"  {SPINNER[frame % len(SPINNER)]} {self.counter}  {self.label}  {took}  "
            room = width - len(head) - 1
            latest = self._latest[:room] if room > 10 else ""
            self._out.write(f"\r\033[K{ui.bold(head)}{ui.gray(latest)}")
            self._out.flush()
            frame += 1
