"""SSH for site users.

Each site user gets:
  - login: your public keys (root's id_ed25519.pub + authorized_keys), so
    whatever logs you in as root also logs you in as the site user;
  - GitHub: a copy of the server's shared GitHub key, so every site can pull
    and push your repos once that one key is on your GitHub account.
Root's own private key is never copied to a site.
"""

import os
import re
import shutil
import socket
from pathlib import Path

from . import db, ui
from .errors import CloudPanelError
from .run import run

ROOT_SSH = Path("/root/.ssh")
ROOT_AUTHORIZED_KEYS = ROOT_SSH / "authorized_keys"
ROOT_PUBLIC_KEY = ROOT_SSH / "id_ed25519.pub"
ROOT_PRIVATE_KEY = ROOT_SSH / "id_ed25519"
SHARED_GITHUB_KEY = ROOT_SSH / "cloudpanel_github_ed25519"
HOME = Path("/home")

# GitHub's published host keys:
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
GITHUB_KNOWN_HOSTS = [
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl",
    "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=",
    "github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=",
]

# "<type> <base64> [comment]" anywhere in an authorized_keys line, so leading
# options (e.g. command="echo 'Please login as ubuntu'") are dropped.
_PUBLIC_KEY = re.compile(
    r"(?:^|\s)((?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp\d+|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)"
    r"\s+[A-Za-z0-9+/]+={0,3}(?:\s+[^\r\n]*)?)$"
)


def key_identity(key):
    """'<type> <base64>' without the comment, used to compare keys."""
    return " ".join(key.split()[:2])


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return None


def ssh_dir(user):
    """/home/<user>/.ssh, created if needed, 0700 and owned by the user."""
    path = HOME / user / ".ssh"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    run(["chown", "-R", f"{user}:{user}", path])
    return path


# --- login keys ---------------------------------------------------------------

def read_login_keys(files=None):
    """Public keys that should log you in as a site user: root's own
    id_ed25519.pub plus root's authorized_keys (options stripped, deduped)."""
    files = files or [ROOT_PUBLIC_KEY, ROOT_AUTHORIZED_KEYS]
    keys, seen = [], set()
    for path in files:
        for line in (_read(path) or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = _PUBLIC_KEY.search(line)
            if match and key_identity(match.group(1)) not in seen:
                seen.add(key_identity(match.group(1)))
                keys.append(match.group(1).strip())
    return keys


def add_authorized_keys(user, keys):
    """Add keys to the user's authorized_keys (deduped). Returns how many were added."""
    directory = ssh_dir(user)
    auth = directory / "authorized_keys"
    lines = [line.strip() for line in (_read(auth) or "").splitlines() if line.strip()]
    have = {key_identity(line) for line in lines}
    added = 0
    for key in keys:
        if key and key_identity(key) not in have:
            lines.append(key.strip())
            have.add(key_identity(key))
            added += 1
    auth.write_text("\n".join(lines) + ("\n" if lines else ""))
    os.chmod(auth, 0o600)
    run(["chown", "-R", f"{user}:{user}", directory])
    return added


def missing_login_keys(user, login_keys=None):
    login_keys = read_login_keys() if login_keys is None else login_keys
    have = {key_identity(line) for line in (_read(HOME / user / ".ssh" / "authorized_keys") or "").splitlines() if line.strip()}
    return [key for key in login_keys if key_identity(key) not in have]


def add_login_keys(user, domain):
    """Give a site user your login keys (and list them in CloudPanel's UI)."""
    keys = read_login_keys()
    if not keys:
        ui.warn(f"No keys found in {ROOT_PUBLIC_KEY} or {ROOT_AUTHORIZED_KEYS}; SSH login as {user} was not set up.")
        return False
    try:
        added = add_authorized_keys(user, keys)
    except (CloudPanelError, OSError) as error:
        ui.warn(f"Could not update authorized_keys for {user}: {error}")
        return False
    try:
        if not db.append_ssh_keys(domain, keys):
            ui.warn(f"{domain} not found in CloudPanel's database; SSH keys not shown in the UI.")
    except Exception as error:  # the UI list is a convenience; login already works
        ui.warn(f"Could not update CloudPanel's SSH key list: {error}")
    ui.success(f"SSH login enabled for {ui.bold(user)} ({added} key{'' if added == 1 else 's'} added)")
    return True


# --- root's key (older versions copied it into sites) -------------------------------

def has_root_key_copy(user):
    """True if the site's ~/.ssh/id_ed25519 is a copy of root's own key."""
    key_file = HOME / user / ".ssh" / "id_ed25519"
    site_pub, root_pub = _read(f"{key_file}.pub"), _read(ROOT_PUBLIC_KEY)
    if site_pub and root_pub and key_identity(site_pub) == key_identity(root_pub):
        return True
    site_priv, root_priv = _read(key_file), _read(ROOT_PRIVATE_KEY)
    return bool(site_priv and root_priv and site_priv.strip() == root_priv.strip())


# --- shared GitHub key ------------------------------------------------------------

def ensure_shared_github_key():
    """Create the server's shared GitHub key if missing. True if just created."""
    pub = Path(f"{SHARED_GITHUB_KEY}.pub")
    if SHARED_GITHUB_KEY.exists() and pub.exists():
        return False
    SHARED_GITHUB_KEY.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(SHARED_GITHUB_KEY.parent, 0o700)
    for path in (SHARED_GITHUB_KEY, pub):
        path.unlink(missing_ok=True)
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"cloudpanel-github@{socket.gethostname()}", "-f", SHARED_GITHUB_KEY])
    os.chmod(SHARED_GITHUB_KEY, 0o600)
    return True


def shared_github_public_key():
    return Path(f"{SHARED_GITHUB_KEY}.pub").read_text().strip()


def fingerprint(pub_file):
    """SHA256:... fingerprint, as GitHub shows it."""
    try:
        return run(["ssh-keygen", "-lf", pub_file]).split()[1]
    except (CloudPanelError, IndexError):
        return None


def uses_shared_github_key(user):
    site = _read(HOME / user / ".ssh" / "id_ed25519.pub")
    shared = _read(f"{SHARED_GITHUB_KEY}.pub")
    return bool(site and shared and key_identity(site) == key_identity(shared))


def install_shared_github_key(user):
    """Copy the shared key into the user's ~/.ssh as id_ed25519 and trust GitHub."""
    directory = ssh_dir(user)
    key_file = directory / "id_ed25519"
    shutil.copyfile(SHARED_GITHUB_KEY, key_file)
    os.chmod(key_file, 0o600)
    shutil.copyfile(f"{SHARED_GITHUB_KEY}.pub", f"{key_file}.pub")
    os.chmod(f"{key_file}.pub", 0o644)

    known_hosts = directory / "known_hosts"
    current = _read(known_hosts) or ""
    missing = [line for line in GITHUB_KNOWN_HOSTS if line not in current]
    if missing:
        prefix = "\n" if current and not current.endswith("\n") else ""
        with open(known_hosts, "a") as handle:
            handle.write(prefix + "\n".join(missing) + "\n")
    os.chmod(known_hosts, 0o644)
    run(["chown", "-R", f"{user}:{user}", directory])


def print_shared_key_instructions(title="New shared GitHub key for this server"):
    print()
    print(ui.bold(title))
    print("Add it to your GitHub account once: Settings > SSH and GPG keys > New SSH key")
    print("(every site on this server uses it to pull and push your repos)")
    print(shared_github_public_key())
    print()


def setup_github_key(user):
    """Give a site the shared GitHub key, creating it the first time."""
    try:
        created = ensure_shared_github_key()
        install_shared_github_key(user)
    except (CloudPanelError, OSError) as error:
        ui.warn(f"Could not install the GitHub key for {user}: {error}")
        return False
    ui.success(f"Shared GitHub key installed for {ui.bold(user)}")
    if created:
        print_shared_key_instructions()
    else:
        ui.note(f"Uses the shared GitHub key {fingerprint(f'{SHARED_GITHUB_KEY}.pub') or ''} "
                f'(already on GitHub if you added it before; "cloudpanel github:key" shows it).')
    return True
