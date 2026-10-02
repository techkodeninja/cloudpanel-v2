"""site:migration:wordpress, site:migration:classicpress and site:migration:novaris.

    cloudpanel site:migration:wordpress --domainName=example.com --stagingName=staging.example.com [--yes]

Copies a staging site over its production site. Production is backed up to
/root/cloudpanel-backups/<domain>/<time>/ first, and you must type the
production domain to go ahead (or pass --yes).
"""

import shutil
import sys
import tempfile
from pathlib import Path

from .. import backup, ui
from ..errors import CloudPanelError, UsageError
from ..run import as_site_user, clpctl, run
from ..sites import htdocs, require, site_username


def _sites(params):
    domain, staging = require(params, "domainName", "stagingName")
    if domain == staging:
        raise UsageError("--domainName and --stagingName must be different sites.")
    prod_user, staging_user = site_username(domain), site_username(staging)
    prod, stage = htdocs(prod_user, domain), htdocs(staging_user, staging)
    for name, path in ((domain, prod), (staging, stage)):
        if not path.is_dir():
            raise CloudPanelError(f"{name} not found ({path} does not exist).")
    return domain, staging, prod_user, staging_user, prod, stage


def _confirm(params, domain, what):
    print(what)
    print(f"A backup of {domain} is saved under {backup.BACKUP_ROOT}/{domain}/ first.")
    if params.get("yes") or ui.confirm_by_typing(f"Type {domain} to continue: ", domain):
        return True
    print("Cancelled; nothing was changed." if sys.stdin.isatty()
          else "Cancelled: no terminal to confirm. Re-run with --yes to skip the question.")
    ui.exit_code = 1
    return False


def _failed(domain, backup_dir, error):
    where = (f"{domain} may be partly changed. Its backup from before the migration is in {backup_dir}"
             if backup_dir else f"Stopped before changing {domain}; nothing was changed.")
    return CloudPanelError(f"Migration failed: {error}\n{where}")


def wordpress(params):
    """WordPress / ClassicPress: wp-content, then the database, then URLs."""
    domain, staging, prod_user, staging_user, prod, stage = _sites(params)
    if not _confirm(params, domain, f"This replaces {ui.bold(domain)}'s database and wp-content with {ui.bold(staging)}'s."):
        return

    # Private (0700) temp folder: other users can't read the dump.
    dump_dir = Path(tempfile.mkdtemp(prefix="cloudpanel-migration-"))
    dump = dump_dir / f"{staging}.sql.gz"
    backup_dir = None
    try:
        staging_db = as_site_user(staging_user, ["wp", "config", "get", "DB_USER", f"--path={stage}"]).strip()
        prod_db = as_site_user(prod_user, ["wp", "config", "get", "DB_USER", f"--path={prod}"]).strip()
        if not staging_db or not prod_db:
            raise CloudPanelError("Could not read DB_USER from wp-config.php.")

        backup_dir = backup.backup_site(domain, database=prod_db, folders=[prod / "wp-content"])
        ui.success(f"Backed up {ui.bold(domain)} to {ui.bold(backup_dir)}")

        clpctl(["db:export", f"--databaseName={staging_db}", f"--file={dump}"])
        ui.success(f"Exported the database from {ui.bold(staging)}.")

        run(["rsync", "-az", "--delete", f"--chown={prod_user}:{prod_user}", f"{stage}/wp-content/", f"{prod}/wp-content"])
        ui.success(f"Copied wp-content from {ui.bold(staging)} to {ui.bold(domain)}.")

        as_site_user(prod_user, ["wp", "db", "reset", "--yes", f"--path={prod}"])
        clpctl(["db:import", f"--databaseName={prod_db}", f"--file={dump}"])
        ui.success(f"Imported the database into {ui.bold(domain)}.")

        as_site_user(prod_user, ["wp", "search-replace", f"//{staging}", f"//{domain}", f"--path={prod}"])
        as_site_user(prod_user, ["wp", "cache", "flush", f"--path={prod}"])
        ui.success(f"{ui.bold(domain)} now runs {staging}'s content.")
    except (CloudPanelError, OSError) as error:
        raise _failed(domain, backup_dir, error) from None
    finally:
        shutil.rmtree(dump_dir, ignore_errors=True)


def novaris(params):
    """Novaris: build staging, point it at production, copy it over."""
    domain, staging, prod_user, staging_user, prod, stage = _sites(params)
    if not _confirm(params, domain, f"This builds {ui.bold(staging)} and replaces all files in {ui.bold(domain)} with the result."):
        return

    built = stage / domain
    backup_dir = None
    try:
        backup_dir = backup.backup_site(domain, folders=[prod])
        ui.success(f"Backed up {ui.bold(domain)} to {ui.bold(backup_dir)}")

        as_site_user(staging_user, ["bash", "-lc", 'source "$HOME/.nvm/nvm.sh" && npm run build'], cwd=stage)
        ui.success(f"Built {ui.bold(staging)}")

        if not built.is_dir():
            raise CloudPanelError(f"The build did not create {built}.")

        # Point the built site at the production domain.
        for file in (built / ".env", built / "config" / "app.php"):
            if file.exists():
                text = file.read_text()
                file.write_text(text.replace(f'APP_URI="https://{staging}"', f'APP_URI="https://{domain}"'))

        run(["rsync", "-az", "--delete", f"--chown={prod_user}:{prod_user}", f"{built}/", str(prod)])
        ui.success(f"Copied the build from {ui.bold(staging)} to {ui.bold(domain)}.")
    except (CloudPanelError, OSError) as error:
        raise _failed(domain, backup_dir, error) from None
