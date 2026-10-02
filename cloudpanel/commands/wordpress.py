"""site:add:wordpress and site:add:classicpress.

    cloudpanel site:add:wordpress --domainName=example.com --wpAdmin=john
        --wpPassword=... --wpEmail=john@example.com
        [--wpType=single|subdirectory|subdomain] [--phpVersion=8.3]
    cloudpanel site:add:classicpress ... same with --cpAdmin/--cpPassword/--cpEmail/--cpType

single is a normal site; subdirectory and subdomain create a multisite
network (sites at example.com/site1 or site1.example.com).
"""

from .. import certificates, node, nginx, system, ui
from ..errors import CloudPanelError, UsageError
from ..run import as_site_user, clpctl
from ..sites import htdocs, nginx_conf, require, site_username
from .sites import setup_site_user

DEFAULT_PHP_VERSION = "8.3"
SITE_TYPES = ("single", "subdirectory", "subdomain")

FLAVORS = {
    "wordpress": {"name": "WordPress", "prefix": "wp", "download": []},
    "classicpress": {"name": "ClassicPress", "prefix": "cp", "download": ["https://www.classicpress.net/latest.zip"]},
}


def check_site_type(option, site_type, domain):
    """Validate the type. A subdomain network must be on the main domain:
    on blog.example.com its sites would be site1.blog.example.com, two levels
    deep, which the certificate and Cloudflare's free proxy don't cover."""
    if site_type not in SITE_TYPES:
        raise UsageError(f"--{option} must be {', '.join(repr(t) for t in SITE_TYPES)} (got {site_type!r}).")
    if site_type == "subdomain" and len(domain.split(".")) != 2:
        main = ".".join(domain.split(".")[-2:])
        raise UsageError(
            f"--{option}=subdomain needs the main domain (e.g. {main}), not {domain}: its sites would be "
            f"site1.{domain}, two levels deep, which the certificate and Cloudflare's proxy don't cover. "
            f"Use subdirectory, or put the network on {main}."
        )


def add_site(params, flavor_key):
    flavor = FLAVORS[flavor_key]
    name, p = flavor["name"], flavor["prefix"]
    domain, admin, password, email = require(params, "domainName", f"{p}Admin", f"{p}Password", f"{p}Email")
    site_type = params.get(f"{p}Type", "single")
    check_site_type(f"{p}Type", site_type, domain)
    php_version = params["phpVersion"] if isinstance(params.get("phpVersion"), str) else DEFAULT_PHP_VERSION

    user = site_username(domain)
    db_password = system.generate_password()
    conf = nginx_conf(domain)
    path = htdocs(user, domain)

    if conf.exists():
        raise CloudPanelError(f"{domain} already exists.")

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

    try:
        nginx.remove_www1(conf, domain)
    except OSError as error:
        ui.warn(f"Could not tidy the nginx config: {error}")

    if site_type == "subdomain":
        try:
            nginx.add_wildcard_server_name(conf, domain)
        except Exception as error:
            raise CloudPanelError(f"Could not set up *.{domain} in nginx: {error}") from None
        ui.success(f"nginx now also serves {ui.bold('*.' + domain)}")

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

    # wp-cli as the site user; every value is passed as-is.
    def wp(*args):
        as_site_user(user, ["wp", *args, f"--path={path}", "--quiet"])

    install_args = [
        f"--url=https://{domain}",
        f"--title={domain}",
        f"--admin_user={admin}",
        f"--admin_password={password}",
        f"--admin_email={email}",
    ]
    try:
        wp("core", "download", *flavor["download"])
        ui.success(f"{name} downloaded.")
        wp("config", "create", "--dbhost=127.0.0.1", f"--dbname={user}", f"--dbuser={user}", f"--dbpass={db_password}")
        ui.success("wp-config.php file created.")
        if site_type == "subdirectory":
            wp("core", "multisite-install", *install_args)
            ui.success(f"{name} Multisite installed successfully.")
        elif site_type == "subdomain":
            wp("core", "multisite-install", "--subdomains", *install_args)
            ui.success(f"{name} Multisite (subdomains) installed successfully.")
        else:
            wp("core", "install", *install_args)
            ui.success(f"{name} installed successfully.")
        wp("config", "set", "DISALLOW_FILE_EDIT", "true", "--raw")
    except CloudPanelError as error:
        raise CloudPanelError(f"{name} install failed for {domain}: {error}") from None

    if not certificates.install_site_certificate(domain):
        ui.exit_code = 1
    node.setup_node(user)
    setup_site_user(user, domain)

    if site_type == "subdomain":
        print()
        print(ui.bold("One more step for subdomain sites"))
        print(f"Make sure *.{domain} reaches this server: a * DNS record (or tunnel route) like the one for {domain}.")


def add_wordpress(params):
    add_site(params, "wordpress")


def add_classicpress(params):
    add_site(params, "classicpress")
