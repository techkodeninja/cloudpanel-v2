"""site:restore:wordpress and site:restore:classicpress.

    cloudpanel site:restore:wordpress --domainName=example.com
        --backupFile=https://downloads.example.com/backup.tar [--phpVersion=8.3]

Creates a NEW site from a CloudPanel backup archive: its htdocs folder and
the newest database dump in it. Never touches an existing site.
"""

from pathlib import Path

from .. import certificates, node, sites, system, ui
from ..errors import CloudPanelError
from ..run import as_site_user, clpctl
from ..sites import nginx_conf, require, site_username
from .sites import setup_site_user
from .wordpress import DEFAULT_PHP_VERSION


def latest_file(directory, suffix):
    """Newest file (by modification time) ending in suffix, anywhere under directory."""
    files = [path for path in Path(directory).rglob(f"*{suffix}") if path.is_file()]
    return max(files, key=lambda path: path.stat().st_mtime, default=None)


def restore(params):
    domain, url = require(params, "domainName", "backupFile")
    php_version = params["phpVersion"] if isinstance(params.get("phpVersion"), str) else DEFAULT_PHP_VERSION
    user = site_username(domain)
    home = sites.HOME / user
    archive = home / "tmp" / "backup.tar"
    extracted = home / "tmp" / "backup"

    if nginx_conf(domain).exists():
        raise CloudPanelError(f"{domain} already exists; restore only creates new sites.")

    try:
        clpctl([
            "site:add:php",
            f"--domainName={domain}",
            f"--phpVersion={php_version}",
            "--vhostTemplate=WordPress",
            f"--siteUser={user}",
            f"--siteUserPassword={system.generate_password()}",
        ])
    except CloudPanelError as error:
        raise CloudPanelError(f"Could not create {domain}: {error}") from None
    ui.success(f"Site {ui.bold(domain)} has been added (PHP {php_version}).")

    def as_user(*args):
        return as_site_user(user, list(args))

    try:
        # Start clean, so a leftover file from an earlier run is never reused.
        as_user("rm", "-rf", archive, extracted)
        as_user("mkdir", "-p", extracted)
        as_user("wget", "-q", "-O", archive, url)
        as_user("tar", "-xf", archive, "-C", extracted)
        ui.success("backup.tar has been downloaded.")

        # The archive holds home/<user>/htdocs and home/<user>/backups/...
        saved = extracted / "home" / user
        as_user("rm", "-rf", home / "htdocs")
        as_user("mv", saved / "htdocs", home / "htdocs")

        db_password = as_user("wp", "config", "get", "DB_PASSWORD", f"--path={home / 'htdocs' / domain}").strip()
        if not db_password:
            raise CloudPanelError("Could not read DB_PASSWORD from the restored wp-config.php.")
        try:
            clpctl([
                "db:add",
                f"--domainName={domain}",
                f"--databaseName={user}",
                f"--databaseUserName={user}",
                f"--databaseUserPassword={db_password}",
            ])
        except CloudPanelError as error:
            raise CloudPanelError(f"Could not create the database for {domain}: {error}") from None
        ui.success(f"Database {ui.bold(user)} has been added.")

        dumps = saved / "backups" / "databases" / user
        dump = latest_file(dumps, ".sql.gz")
        if not dump:
            raise CloudPanelError(f"No .sql.gz file found in the archive ({dumps}).")
        clpctl(["db:import", f"--databaseName={user}", f"--file={dump}"])
        ui.success(f"Database {ui.bold(user)} has been imported ({dump.name}).")
    except CloudPanelError as error:
        raise CloudPanelError(f"Restore of {domain} failed: {error}") from None
    finally:
        try:
            as_user("rm", "-rf", archive, extracted)
        except CloudPanelError as error:
            ui.warn(f"Could not remove {archive}: {error}")

    if not certificates.install_site_certificate(domain):
        ui.exit_code = 1
    node.setup_node(user)
    setup_site_user(user, domain)
    ui.success(f"{ui.bold(domain)} has been restored.")


def restore_wordpress(params):
    restore(params)


def restore_classicpress(params):
    restore(params)
