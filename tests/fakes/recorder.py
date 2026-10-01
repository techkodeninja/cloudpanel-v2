#!/usr/bin/env python3
"""Stand-in for clpctl, sudo, wp, certbot, ... used by the tests.

Records exactly which arguments it received (one JSON list per line) to the
file in $CP_TEST_LOG, prints $CP_FAKE_OUTPUT if set, and exits with
$CP_FAKE_EXIT (default 0). Symlink it under the real command's name.
"""

import json
import os
import sys

name = os.path.basename(sys.argv[0])
stdin = ""
if not sys.stdin.isatty():
    try:
        stdin = sys.stdin.read()
    except OSError:
        stdin = ""

with open(os.environ["CP_TEST_LOG"], "a") as log:
    log.write(json.dumps({"argv": [name, *sys.argv[1:]], "cwd": os.getcwd(), "stdin": stdin}) + "\n")

sys.stdout.write(os.environ.get("CP_FAKE_OUTPUT", ""))
code = int(os.environ.get("CP_FAKE_EXIT", "0"))
if code:
    sys.stderr.write(os.environ.get("CP_FAKE_ERROR", "fake failure") + "\n")
sys.exit(code)
