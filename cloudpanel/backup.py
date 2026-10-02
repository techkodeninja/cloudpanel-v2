"""Backups made before a migration overwrites a site."""

import os
from datetime import datetime, timezone
from pathlib import Path

from .run import clpctl, run

BACKUP_ROOT = Path("/root/cloudpanel-backups")


def backup_site(domain, database=None, folders=()):
    """Back up a site's database (if given) and folders into
    BACKUP_ROOT/<domain>/<timestamp>/ (root-only). Returns that folder.

    Raises if any part fails, so the caller stops before changing anything.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    directory = BACKUP_ROOT / domain / stamp
    directory.mkdir(parents=True, mode=0o700)
    for path in (BACKUP_ROOT, BACKUP_ROOT / domain, directory):
        os.chmod(path, 0o700)

    if database:
        clpctl(["db:export", f"--databaseName={database}", f"--file={directory / f'{database}.sql.gz'}"])

    for folder in map(Path, folders):
        if folder.exists():
            run(["tar", "-czf", directory / f"{folder.name}.tar.gz", "-C", folder.parent, folder.name])

    return directory
