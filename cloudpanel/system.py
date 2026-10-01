"""Small facts about this server, and generated passwords."""

import secrets
import socket
import string
from pathlib import Path

PASSWORD_CHARS = string.ascii_letters + string.digits


def os_id(os_release=Path("/etc/os-release")):
    """Distribution ID from /etc/os-release, e.g. 'ubuntu' or 'debian'."""
    try:
        for line in os_release.read_text().splitlines():
            if line.startswith("ID="):
                return line[3:].strip().strip('"').lower() or None
    except OSError:
        pass
    return None


def generate_password(length=15):
    """Random password of letters and digits (safe for every tool it's passed to)."""
    return "".join(secrets.choice(PASSWORD_CHARS) for _ in range(length))


def server_ip():
    """This server's main IP address, or None if it can't be found.

    Opens a UDP socket towards a public address to see which local address
    the system would use; nothing is actually sent.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("1.1.1.1", 53))
            return probe.getsockname()[0]
    except OSError:
        return None
