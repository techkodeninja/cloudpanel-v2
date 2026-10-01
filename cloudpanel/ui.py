"""Terminal output: colored status lines and the type-to-confirm prompt.

Colors are only used when printing to a terminal and NO_COLOR is not set,
so logs and pipes get plain text.
"""

import os
import sys

_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ

# Set to 1 by fail(); the CLI uses it as the exit code.
exit_code = 0


def _style(code, text):
    return f"\033[{code}m{text}\033[0m" if _COLOR else str(text)


def bold(text):
    return _style("1", text)


def green(text):
    return _style("1;32", text)


def yellow(text):
    return _style("1;33", text)


def red(text):
    return _style("1;31", text)


def gray(text):
    return _style("90", text)


def success(message):
    print(f"{green('✔ Success:')} {message}", flush=True)


def warn(message):
    print(f"{yellow('⚠ Warning:')} {message}", flush=True)


def info(message):
    print(f"{bold('ℹ')} {message}", flush=True)


def fail(message):
    """Report a failed step and make the run exit with status 1."""
    global exit_code
    exit_code = 1
    print(f"{red('✖ Error:')} {message}", file=sys.stderr, flush=True)


def note(message):
    print(gray(f"  {message}"), flush=True)


def confirm_by_typing(prompt, expected):
    """True only if the person types `expected` exactly.

    Without a terminal (cron, CI) nobody can answer, so this returns False;
    callers offer --yes for that case.
    """
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip() == expected
