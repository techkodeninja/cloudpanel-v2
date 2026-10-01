"""CloudPanel's own SQLite database (read sites, show SSH keys in the UI)."""

import sqlite3
from pathlib import Path

DB_PATH = Path("/home/clp/htdocs/app/data/db.sq3")


def _connect(read_only=True):
    mode = "ro" if read_only else "rw"
    return sqlite3.connect(f"file:{DB_PATH}?mode={mode}", uri=True)


def list_sites():
    """All sites: [{'id', 'domain', 'user', 'application'}], oldest first."""
    with _connect() as db:
        rows = db.execute("SELECT id, domain_name, user, application FROM site ORDER BY id ASC").fetchall()
    return [{"id": r[0], "domain": r[1], "user": r[2], "application": r[3]} for r in rows]


def site_exists(domain):
    with _connect() as db:
        return db.execute("SELECT 1 FROM site WHERE domain_name = ?", (domain,)).fetchone() is not None


def append_ssh_keys(domain, keys):
    """Add public keys to the site's SSH key list in CloudPanel (deduped).

    Returns False if the domain isn't in the database.
    """
    with _connect(read_only=False) as db:
        row = db.execute("SELECT ssh_keys FROM site WHERE domain_name = ?", (domain,)).fetchone()
        if row is None:
            return False
        lines = [line.strip() for line in (row[0] or "").splitlines() + list(keys) if line.strip()]
        merged = list(dict.fromkeys(lines))
        value = "\n".join(merged) + ("\n" if merged else "")
        db.execute("UPDATE site SET ssh_keys = ? WHERE domain_name = ?", (value, domain))
    return True
