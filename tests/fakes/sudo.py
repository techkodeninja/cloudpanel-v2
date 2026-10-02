#!/usr/bin/env python3
"""Stand-in sudo for tests.

Records every call like the other fakes. `sudo -u USER -H git config
--global [--get] KEY [VALUE]` is emulated with a small per-user store, so
"set it, then check it" behaves like the real thing. Everything else just
succeeds.
"""

import json
import os
import sys

args = sys.argv[1:]
log_path = os.environ["CP_TEST_LOG"]
with open(log_path, "a") as log:
    log.write(json.dumps({"argv": ["sudo", *args], "cwd": os.getcwd(), "stdin": ""}) + "\n")

user = args[args.index("-u") + 1] if "-u" in args else "root"
command = args[args.index("-H") + 1:] if "-H" in args else args

if command[:3] == ["git", "config", "--global"]:
    store_path = os.path.join(os.path.dirname(log_path), f"gitconfig-{user}.json")
    store = json.load(open(store_path)) if os.path.exists(store_path) else {}
    rest = command[3:]
    if rest[:1] == ["--get"]:
        if rest[1] in store:
            print(store[rest[1]])
            sys.exit(0)
        sys.exit(1)  # git exits 1 when the key isn't set
    store[rest[0]] = rest[1]
    json.dump(store, open(store_path, "w"))
