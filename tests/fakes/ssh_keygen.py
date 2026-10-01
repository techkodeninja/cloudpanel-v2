#!/usr/bin/env python3
"""Stand-in ssh-keygen for tests.

`-f FILE` writes a fake key pair (FILE, FILE.pub) using the -C comment;
`-lf FILE` prints a fingerprint derived from the public key. Every call is
also recorded like the other fakes.
"""

import hashlib
import json
import os
import secrets
import sys

args = sys.argv[1:]
with open(os.environ["CP_TEST_LOG"], "a") as log:
    log.write(json.dumps({"argv": ["ssh-keygen", *args], "cwd": os.getcwd(), "stdin": ""}) + "\n")

if "-lf" in args:
    pub = open(args[args.index("-lf") + 1]).read().split()[1]
    print(f"256 SHA256:{hashlib.sha256(pub.encode()).hexdigest()[:16]} x (ED25519)")
    sys.exit(0)

path = args[args.index("-f") + 1]
comment = args[args.index("-C") + 1] if "-C" in args else ""
token = secrets.token_hex(12)
with open(path, "w") as private:
    private.write(f"-----BEGIN OPENSSH PRIVATE KEY-----\nFAKE{token}\n-----END OPENSSH PRIVATE KEY-----\n")
os.chmod(path, 0o600)
with open(f"{path}.pub", "w") as public:
    public.write(f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5{token} {comment}\n")
