"""site:staging:create: a private copy of a live WordPress/ClassicPress site.

    cloudpanel site:staging:create --domainName=example.com
        [--stagingName=staging.example.com] [--phpVersion=8.3] [--yes]

Copies example.com's files and database to staging.example.com (creating
that site the first time), points the copy at the staging address, and
keeps it private: only people logged in to WordPress can see it, search
engines are told not to index it, and it never sends email. Log in with
the same WordPress account as the live site.

When the changes are ready, push them to the live site with
site:migration:wordpress (--filesOnly to leave the live database alone).
"""

import re
import shutil
import sys
import tempfile
from pathlib import Path

from .. import certificates, node, nginx, system, ui
from ..errors import CloudPanelError, UsageError
from ..run import as_site_user, clpctl, run
from ..sites import htdocs, nginx_conf, require, site_username
from .sites import setup_site_user
from .wordpress import DEFAULT_PHP_VERSION

GUARD_NAME = "cloudpanel-staging.php"
_DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


def guard_plugin(staging):
    """A must-use plugin that keeps the staging copy private.

    It checks its own address first, so if it is ever copied to the live
    site it does nothing there (the migration also leaves it out)."""
    return f"""<?php
/**
 * Plugin Name: CloudPanel staging guard
 * Description: Keeps this staging copy private: only logged-in users can see it, search engines are told not to index it, and it never sends email. Added by cloudpanel site:staging:create. Does nothing on any address other than {staging}.
 */

if (!defined('ABSPATH')) {{
    exit;
}}
if (strtolower((string) parse_url(home_url(), PHP_URL_HOST)) !== '{staging}') {{
    return;
}}

// Never email real customers or subscribers from the copy.
add_filter('pre_wp_mail', '__return_false');

// Search engines: don't index, even if someone changes the setting.
add_filter('pre_option_blog_public', fn() => '0');
add_action('send_headers', function () {{
    header('X-Robots-Tag: noindex, nofollow', true);
}});

// Visitors: log in first (the same WordPress account as the live site).
add_action('template_redirect', function () {{
    if (!is_user_logged_in()) {{
        auth_redirect();
    }}
}});
add_filter('rest_authentication_errors', function ($result) {{
    if ($result === null && !is_user_logged_in()) {{
        return new WP_Error('staging_private', 'This staging site is private.', ['status' => 401]);
    }}
    return $result;
}});
"""


def _check_domain(option, value):
    if not _DOMAIN.match(value):
        raise UsageError(f"--{option} must be a plain domain name like example.com (got {value!r}).")


def _is_on(value):
    return value.strip().lower() in ("1", "true", "yes", "on")


def create(params):
    (domain,) = require(params, "domainName")
    domain = domain.lower()
    staging = params.get("stagingName")
    if staging is True:
        raise UsageError("--stagingName needs a value: --stagingName=staging.example.com")
    staging = (staging or f"staging.{domain}").strip().lower()
    _check_domain("domainName", domain)
    _check_domain("stagingName", staging)
    if staging == domain:
        raise UsageError("--stagingName must be a different site from --domainName.")
    php_version = params["phpVersion"] if isinstance(params.get("phpVersion"), str) else DEFAULT_PHP_VERSION

    live_user, staging_user = site_username(domain), site_username(staging)
    live, stage = htdocs(live_user, domain), htdocs(staging_user, staging)
    if not (live / "wp-config.php").is_file():
        raise CloudPanelError(f"{domain} isn't a WordPress or ClassicPress site ({live / 'wp-config.php'} not found).")

    def live_wp(*args):
        return as_site_user(live_user, ["wp", *args, f"--path={live}"]).strip()

    def stage_wp(*args):
        return as_site_user(staging_user, ["wp", *args, f"--path={stage}", "--quiet"])

    try:
        multisite = _is_on(live_wp("config", "get", "MULTISITE"))
    except CloudPanelError:
        multisite = False  # wp exits 1 when MULTISITE isn't defined
    if multisite:
        raise CloudPanelError(f"{domain} is a multisite network; staging copies of networks aren't supported yet.")

    exists = nginx_conf(staging).exists()
    if exists:
        if not (stage / "wp-config.php").is_file():
            raise CloudPanelError(f"{staging} already exists but isn't a WordPress site. Pick another --stagingName.")
        print(f"This replaces everything on {ui.bold(staging)} (files and database) with a fresh copy of {ui.bold(domain)}.")
        print(f"{domain} itself is not changed.")
        if not (params.get("yes") or ui.confirm_by_typing(f"Type {staging} to continue: ", staging)):
            print("Cancelled; nothing was changed." if sys.stdin.isatty()
                  else "Cancelled: no terminal to confirm. Re-run with --yes to skip the question.")
            ui.exit_code = 1
            return

    live_db = live_wp("config", "get", "DB_NAME")
    if not live_db:
        raise CloudPanelError(f"Could not read DB_NAME from {domain}'s wp-config.php.")

    if exists:
        def stage_config(key):
            return as_site_user(staging_user, ["wp", "config", "get", key, f"--path={stage}"]).strip()
        stage_db, stage_db_user, stage_db_password = (stage_config(k) for k in ("DB_NAME", "DB_USER", "DB_PASSWORD"))
        if not (stage_db and stage_db_user and stage_db_password):
            raise CloudPanelError(f"Could not read the database settings from {staging}'s wp-config.php.")
    else:
        stage_db = stage_db_user = staging_user
        stage_db_password = system.generate_password()
        _create_site(staging, staging_user, php_version, stage_db_password)

    dump_dir = Path(tempfile.mkdtemp(prefix="cloudpanel-staging-"))  # 0700: only root can read the dump
    dump = dump_dir / f"{domain}.sql.gz"
    try:
        clpctl(["db:export", f"--databaseName={live_db}", f"--file={dump}"])
        ui.success(f"Exported the database from {ui.bold(domain)}.")

        run(["rsync", "-az", "--delete", f"--chown={staging_user}:{staging_user}",
             f"--exclude=/wp-content/mu-plugins/{GUARD_NAME}", f"{live}/", str(stage)])
        ui.success(f"Copied the files from {ui.bold(domain)}.")

        # The copied wp-config.php keeps the live site's settings; only the
        # database and address change.
        for key, value in (("DB_NAME", stage_db), ("DB_USER", stage_db_user),
                           ("DB_PASSWORD", stage_db_password), ("DB_HOST", "127.0.0.1")):
            stage_wp("config", "set", key, value)
        config = (stage / "wp-config.php").read_text(errors="replace")
        for key in ("WP_HOME", "WP_SITEURL"):
            if re.search(rf"""define\(\s*['"]{key}['"]""", config):
                stage_wp("config", "set", key, f"https://{staging}")
        stage_wp("config", "set", "WP_ENVIRONMENT_TYPE", "staging")

        stage_wp("db", "reset", "--yes")
        clpctl(["db:import", f"--databaseName={stage_db}", f"--file={dump}"])
        stage_wp("search-replace", f"//{domain}", f"//{staging}")
        stage_wp("option", "update", "blog_public", "0")
        ui.success(f"Copied the database and pointed it at {ui.bold(staging)}.")

        # Written as the site user, never by root into a folder the site owns.
        mu_plugins = stage / "wp-content" / "mu-plugins"
        as_site_user(staging_user, ["mkdir", "-p", mu_plugins])
        as_site_user(staging_user, ["tee", mu_plugins / GUARD_NAME], input=guard_plugin(staging))
        stage_wp("cache", "flush")
        ui.success("Made the copy private: login required, hidden from search engines, no email.")
    except (CloudPanelError, OSError) as error:
        raise CloudPanelError(f"Staging copy failed: {error}\n{domain} was not changed; "
                              f"{staging} may be incomplete. Run this again to retry.") from None
    finally:
        shutil.rmtree(dump_dir, ignore_errors=True)

    if not exists:
        node.setup_node(staging_user)
        setup_site_user(staging_user, staging)

    print()
    print(ui.bold(f"Staging is ready: https://{staging}"))
    print(f"  Log in with your {domain} WordPress account.")
    print("  Nobody else can see it, search engines skip it, and it sends no email.")
    print()
    print("When your changes are ready, push them to the live site:")
    print(f"  cloudpanel site:migration:wordpress --domainName='{domain}' --stagingName='{staging}' --filesOnly")
    print(ui.gray("  --filesOnly: themes and plugins only; keeps the live database (orders, comments, sign-ups)."))
    print(ui.gray("  Without it the live database is replaced with staging's as well."))


def _create_site(staging, user, php_version, db_password):
    try:
        clpctl([
            "site:add:php",
            f"--domainName={staging}",
            f"--phpVersion={php_version}",
            "--vhostTemplate=WordPress",
            f"--siteUser={user}",
            f"--siteUserPassword={system.generate_password()}",
        ])
    except CloudPanelError as error:
        raise CloudPanelError(f"Could not create {staging}: {error}") from None
    ui.success(f"Site {ui.bold(staging)} has been added (PHP {php_version}).")
    try:
        nginx.remove_www1(nginx_conf(staging), staging)
    except OSError as error:
        ui.warn(f"Could not tidy the nginx config: {error}")
    try:
        clpctl([
            "db:add",
            f"--domainName={staging}",
            f"--databaseName={user}",
            f"--databaseUserName={user}",
            f"--databaseUserPassword={db_password}",
        ])
    except CloudPanelError as error:
        raise CloudPanelError(f"Could not create the database for {staging}: {error}") from None
    ui.success(f"Database {ui.bold(user)} has been added.")
    if not certificates.install_site_certificate(staging):
        ui.exit_code = 1
