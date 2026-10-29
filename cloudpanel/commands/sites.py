"""site:add:php, site:add:static, site:add:reverse-proxy.

Also the shared steps for commands that create a site and then set it up
(WordPress, ClassicPress, restores, staging): the database, and removing a
site again when setting it up fails part-way.
"""

from contextlib import contextmanager

from .. import certificates, git_identity, node, ssh, system, ui
from ..errors import CloudPanelError
from ..run import clpctl
from ..sites import nginx_conf, require, site_username

PHP_DEFAULT_VERSION = "8.4"


def _create_site(domain, clpctl_args):
    """Run clpctl to create the site; stop with a clear error if it can't."""
    if nginx_conf(domain).exists():
        raise CloudPanelError(f"{domain} already exists.")
    try:
        clpctl(clpctl_args)
    except CloudPanelError as error:
        raise CloudPanelError(f"Could not create {domain}: {error}") from None


MYSQL_ROOT_REFUSED = "Access denied for user 'root'"


def add_database(domain, name, user, password):
    """clpctl db:add, with a clear explanation when MySQL refuses CloudPanel's root login."""
    try:
        clpctl([
            "db:add",
            f"--domainName={domain}",
            f"--databaseName={name}",
            f"--databaseUserName={user}",
            f"--databaseUserPassword={password}",
        ])
    except CloudPanelError as error:
        message = f"Could not create the database for {domain}: {error}"
        if MYSQL_ROOT_REFUSED in str(error):
            message += (
                "\nMySQL refused CloudPanel's root password, so no database can be created on this server "
                "until it matches. Compare the password from \"clpctl db:show:master-credentials\" with MySQL's."
            )
        raise CloudPanelError(message) from None
    ui.success(f"Database {ui.bold(name)} has been added.")


def undo_new_site(domain):
    """Delete a site this command has just created, so it can simply be run again."""
    try:
        clpctl(["site:delete", f"--domainName={domain}"], input="yes\n")
    except CloudPanelError as error:
        ui.warn(f"Could not remove the half-made {domain} ({error}). "
                f"Delete it with: cloudpanel site:delete --domainName='{domain}'")
        return
    ui.info(f"Removed the half-made {ui.bold(domain)}, so the same command can be run again once this is fixed.")


@contextmanager
def removed_on_failure(domain):
    """Around the setup of a site this command just created: if a step fails
    (or Ctrl-C), delete the site again instead of leaving half of it."""
    try:
        yield
    except (CloudPanelError, OSError, KeyboardInterrupt):
        undo_new_site(domain)
        raise


def _certificate(domain):
    if not certificates.install_site_certificate(domain):
        ui.exit_code = 1


def setup_site_user(user, domain):
    """Login keys, git name/email and the shared GitHub key for a site user."""
    ssh.add_login_keys(user, domain)
    git_identity.setup_identity(user)
    ssh.setup_github_key(user)


def add_php(params):
    """cloudpanel site:add:php --domainName=example.com [--phpVersion=8.4]"""
    (domain,) = require(params, "domainName")
    php_version = params.get("phpVersion") if isinstance(params.get("phpVersion"), str) else PHP_DEFAULT_VERSION
    user = site_username(domain)

    _create_site(domain, [
        "site:add:php",
        f"--domainName={domain}",
        f"--phpVersion={php_version}",
        "--vhostTemplate=Generic",
        f"--siteUser={user}",
        f"--siteUserPassword={system.generate_password()}",
    ])
    ui.success(f"Site {ui.bold(domain)} has been created (PHP {php_version}).")

    _certificate(domain)
    node.setup_node(user)
    setup_site_user(user, domain)


def add_static(params):
    """cloudpanel site:add:static --domainName=example.com"""
    (domain,) = require(params, "domainName")
    user = site_username(domain)

    _create_site(domain, [
        "site:add:static",
        f"--domainName={domain}",
        f"--siteUser={user}",
        f"--siteUserPassword={system.generate_password()}",
    ])
    ui.success(f"Site {ui.bold(domain)} has been added.")
    _certificate(domain)


def add_reverse_proxy(params):
    """cloudpanel site:add:reverse-proxy --domainName=example.com --reverseProxyUrl=http://127.0.0.1:8000"""
    domain, proxy_url = require(params, "domainName", "reverseProxyUrl")
    user = site_username(domain)

    _create_site(domain, [
        "site:add:reverse-proxy",
        f"--domainName={domain}",
        f"--reverseProxyUrl={proxy_url}",
        f"--siteUser={user}",
        f"--siteUserPassword={system.generate_password()}",
    ])
    ui.success(f"{ui.bold(domain)} has been added.")
    _certificate(domain)
