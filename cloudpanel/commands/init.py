"""init:install: set up a fresh Ubuntu/Debian server with CloudPanel.

    cloudpanel init:install --cfToken='...' [--installerSha256=<hash>]

1. Installs Certbot and its Cloudflare DNS plugin (snap).
2. Saves the Cloudflare API token to /root/.cloudflare/token (root-only).
3. Downloads the CloudPanel installer, checks its SHA-256 against the value
   CloudPanel publishes, and only then runs it.
4. Creates the admin user with a generated password, prints it once and
   saves it to /root/.cloudflare/admin (root-only).
"""

import hashlib
import os
import shutil
import tempfile
import urllib.request
from datetime import date
from pathlib import Path

from .. import system, ui
from ..errors import CloudPanelError
from ..run import clpctl, command_exists, run

# SHA-256 of the installer as published on
# https://www.cloudpanel.io/docs/v2/getting-started/other/ (checked 2026-10-01).
# When CloudPanel publishes a new installer, update this value, or pass
# --installerSha256=<new hash> for a one-off install.
INSTALLER_URL = "https://installer.cloudpanel.io/ce/v2/install.sh"
INSTALLER_SHA256 = "8146dbe0a488e7088b04071b0c34d59aa0ab1fe9dcec382d395fd155c9e6c476"
INSTALLER_DOCS = "https://www.cloudpanel.io/docs/v2/getting-started/other/"

CLOUDFLARE_DIR = Path("/root/.cloudflare")
TOKEN_FILE = CLOUDFLARE_DIR / "token"
ADMIN_FILE = CLOUDFLARE_DIR / "admin"
CERTBOT = Path("/usr/bin/certbot")

# CloudPanel admin created on install. The password is generated per server.
ADMIN_USER = "cpwarren"
ADMIN_EMAIL = "benlumia007@gmail.com"


def install(params):
    cf_token = params.get("cfToken")
    expected_sha256 = params.get("installerSha256") or INSTALLER_SHA256
    if expected_sha256 is True:
        raise CloudPanelError("--installerSha256 needs a value: --installerSha256=<hash>")

    install_certbot()
    save_cloudflare_token(cf_token)

    if command_exists("clpctl"):
        ui.warn("CloudPanel is already installed; skipping the installer and admin user.")
        return

    if not install_cloudpanel(expected_sha256.lower()):
        return
    create_admin_user()


def install_certbot():
    """Certbot + Cloudflare DNS plugin via snap (Debian needs snapd first)."""
    if CERTBOT.exists():
        return
    try:
        if system.os_id() == "debian" and not command_exists("snap"):
            run(["apt-get", "update"])
            run(["apt-get", "install", "-y", "snapd"])
        run(["snap", "install", "--classic", "certbot"])
        if not CERTBOT.exists() and not CERTBOT.is_symlink():
            CERTBOT.symlink_to("/snap/bin/certbot")
        run(["snap", "set", "certbot", "trust-plugin-with-root=ok"])
        run(["snap", "install", "certbot-dns-cloudflare"])
        ui.success("Certbot and its Cloudflare DNS plugin are installed.")
    except (CloudPanelError, OSError) as error:
        ui.fail(f"Could not install Certbot: {error}")
        raise CloudPanelError("Certbot is needed for certificates; fix the error above and run init:install again.") from None


def save_cloudflare_token(cf_token):
    if TOKEN_FILE.exists():
        if isinstance(cf_token, str) and cf_token.strip():
            ui.info(f"{TOKEN_FILE} already exists; the --cfToken given was not saved.")
        return

    if not (isinstance(cf_token, str) and cf_token.strip()):
        ui.warn(f"No --cfToken given and {TOKEN_FILE} does not exist; certificates will fail until it is set.")
        return

    write_private_file(TOKEN_FILE, f"# Cloudflare API token used by Certbot\ndns_cloudflare_api_token = {cf_token.strip()}\n")
    ui.success(f"Cloudflare token saved to {TOKEN_FILE} (root-only).")


def install_cloudpanel(expected_sha256):
    """Download, verify and run the installer. Returns True on success."""
    work_dir = Path(tempfile.mkdtemp(prefix="cloudpanel-install-"))
    installer = work_dir / "install.sh"
    try:
        try:
            with urllib.request.urlopen(INSTALLER_URL, timeout=60) as response:
                installer.write_bytes(response.read())
        except OSError as error:
            ui.fail(f"Could not download the CloudPanel installer: {error}")
            return False

        actual = hashlib.sha256(installer.read_bytes()).hexdigest()
        if actual != expected_sha256:
            ui.fail("The CloudPanel installer does not match its expected checksum, so it was not run.")
            print(f"  expected {expected_sha256}")
            print(f"  got      {actual}")
            print(f"If CloudPanel has published a new installer, check the hash on {INSTALLER_DOCS}")
            print("and re-run with --installerSha256=<published hash>.")
            return False
        ui.success("CloudPanel installer checksum verified.")

        try:
            run(["bash", str(installer)], show=True, cwd=work_dir)
        except CloudPanelError as error:
            ui.fail(f"The CloudPanel installer failed: {error}")
            return False
        ui.success("CloudPanel installation completed.")
        return True
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def create_admin_user():
    password = system.generate_password(24)
    try:
        clpctl([
            "user:add",
            f"--userName={ADMIN_USER}",
            f"--email={ADMIN_EMAIL}",
            "--firstName=System",
            "--lastName=Administrator",
            f"--password={password}",
            "--role=admin",
            "--status=1",
        ])
    except CloudPanelError as error:
        ui.fail(f"Could not create admin user {ADMIN_USER}: {error}")
        return
    ui.success(f"User {ui.bold(ADMIN_USER)} has been created.")

    url = f"https://{system.server_ip() or '<server-ip>'}:8443"
    try:
        write_private_file(ADMIN_FILE, "\n".join([
            f"# CloudPanel admin, created {date.today().isoformat()}",
            f"CP_URL={url}",
            f"CP_ADMIN={ADMIN_USER}",
            f"CP_EMAIL={ADMIN_EMAIL}",
            f"CP_PASSWORD={password}",
            "",
        ]))
        ui.success(f"Admin login saved to {ADMIN_FILE}")
    except OSError as error:
        ui.warn(f"Could not save {ADMIN_FILE}: {error}. Copy the password below now.")

    # Always show the login once so it can go into a password manager.
    print()
    print(ui.bold("CloudPanel admin login"))
    print(f"  URL:      {url}")
    print(f"  User:     {ADMIN_USER}")
    print(f"  Password: {password}")
    print()


def write_private_file(path, content):
    """Write a file that only root can read, in a folder only root can open."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content)
    os.chmod(path, 0o600)
