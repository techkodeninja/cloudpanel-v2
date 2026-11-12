"""init:install: set up a fresh Ubuntu/Debian server with CloudPanel.

    cloudpanel init:install --cfToken='...' [--installerSha256=<hash>] [--panelDomain=panel.example.com]

1. Installs Certbot and its Cloudflare DNS plugin (apt), and a hook so
   renewed certificates go onto the sites that use them.
2. Saves the Cloudflare API token to /root/.cloudflare/token (root-only).
3. Downloads the CloudPanel installer, checks its SHA-256 against the value
   CloudPanel publishes, and only then runs it.
4. Creates the admin user with a generated password, prints it once and
   saves it to /root/.cloudflare/admin (root-only).
5. With --panelDomain, puts the admin on that domain (see panel:domain).

Each stage shows as a numbered step; command output goes to
/root/.cloudpanel/install-<time>.log instead of the screen.
"""

import hashlib
import os
import shutil
import tempfile
import urllib.request
from datetime import date, datetime
from pathlib import Path

from .. import certificates, progress, system, ui
from ..errors import CloudPanelError
from ..run import clpctl, command_exists

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
LOG_DIR = Path("/root/.cloudpanel")

# CloudPanel admin created on install. The password is generated per server.
ADMIN_USER = "cpwarren"
ADMIN_EMAIL = "benlumia007@gmail.com"


def install(params):
    cf_token = params.get("cfToken")
    expected_sha256 = params.get("installerSha256") or INSTALLER_SHA256
    if expected_sha256 is True:
        raise CloudPanelError("--installerSha256 needs a value: --installerSha256=<hash>")
    panel_domain = _panel_domain(params)
    already_installed = command_exists("clpctl")

    log_path = LOG_DIR / f"install-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    steps = progress.Steps(6 + bool(panel_domain), log_path)
    print(ui.bold("CloudPanel installer"))
    print(ui.gray(f"Log: {log_path}"))
    print()

    login = None
    work_dir = Path(tempfile.mkdtemp(prefix="cloudpanel-install-"))
    try:
        with steps.step("Installing Certbot") as step:
            install_certbot(step)
        with steps.step("Certificate renewal hook") as step:
            step.detail = "installed" if certificates.ensure_renewal_hook() else "already set up"
        with steps.step("Saving Cloudflare token") as step:
            step.detail = save_cloudflare_token(cf_token)

        if already_installed:
            for label in ("Downloading CloudPanel installer", "Installing CloudPanel", "Creating admin user"):
                with steps.step(label) as step:
                    step.detail = "skipped: already installed"
        else:
            installer = work_dir / "install.sh"
            with steps.step("Downloading CloudPanel installer") as step:
                download_installer(installer, expected_sha256.lower())
                step.detail = "checksum verified"
            with steps.step("Installing CloudPanel", long=True) as step:
                try:
                    step.run(["bash", installer], cwd=work_dir)
                except CloudPanelError as error:
                    raise CloudPanelError(f"The CloudPanel installer failed: {error}") from None
            with steps.step("Creating admin user") as step:
                login = create_admin_user()
                step.detail = ADMIN_USER

        if panel_domain:
            from . import panel
            with steps.step("Panel domain") as step:
                step.detail = panel_domain
                panel.set_domain(panel_domain)
    except CloudPanelError as error:
        raise CloudPanelError(f"{error}\nFull log: {log_path}") from None
    finally:
        steps.close()
        shutil.rmtree(work_dir, ignore_errors=True)

    print()
    if login:
        url, password = login
        # Shown once, never written to the log, so it can go into a password manager.
        print(ui.bold("CloudPanel admin login"))
        print(f"  URL:      {url}")
        print(f"  User:     {ADMIN_USER}")
        print(f"  Password: {password}")
        print(ui.gray(f"  Also saved to {ADMIN_FILE} (root-only)."))
    elif already_installed:
        ui.warn("CloudPanel was already installed; the installer and admin user were skipped.")
    print(ui.gray(f"Log: {log_path}"))


def _panel_domain(params):
    """--panelDomain, checked before anything is installed."""
    if "panelDomain" not in params:
        return None
    value = params["panelDomain"]
    if not isinstance(value, str) or not value.strip():
        raise CloudPanelError("--panelDomain needs a value: --panelDomain=panel.example.com")
    from . import panel
    return panel._normalize(value)


def install_certbot(step):
    """Certbot + its Cloudflare DNS plugin from the distribution's packages.

    apt, not snap: the snap store is often blocked by filtering firewalls,
    while apt uses the distribution's own mirrors (which CloudPanel's
    installer needs anyway). A Certbot already installed (e.g. by snap on an
    older server) is left as it is."""
    if CERTBOT.exists():
        step.detail = "already installed"
        return
    # Wait for the package lock instead of failing: on a fresh server,
    # unattended upgrades often hold it for the first few minutes.
    apt = ["apt-get", "-o", "DPkg::Lock::Timeout=600"]
    try:
        step.run([*apt, "update"])
        step.run([*apt, "install", "-y", "--no-install-recommends", "certbot", "python3-certbot-dns-cloudflare"])
    except (CloudPanelError, OSError) as error:
        raise CloudPanelError(f"Could not install Certbot: {error}\n"
                              "Certbot is needed for certificates; fix the error above and run init:install again.") from None
    step.detail = "with the Cloudflare plugin"


def save_cloudflare_token(cf_token):
    """Save the token unless one is already saved. Returns a short status."""
    if TOKEN_FILE.exists():
        if isinstance(cf_token, str) and cf_token.strip():
            ui.info(f"{TOKEN_FILE} already exists; the --cfToken given was not saved. "
                    "To replace it: cloudpanel cloudflare:token")
        return "already saved"

    if not (isinstance(cf_token, str) and cf_token.strip()):
        ui.warn(f"No --cfToken given and {TOKEN_FILE} does not exist; certificates will fail until it is set "
                "(cloudpanel cloudflare:token).")
        return "not given"

    write_private_file(TOKEN_FILE, f"# Cloudflare API token used by Certbot\ndns_cloudflare_api_token = {cf_token.strip()}\n")
    return f"saved to {TOKEN_FILE}"


def download_installer(installer, expected_sha256):
    """Download the installer and check its checksum (raises if it doesn't match)."""
    try:
        with urllib.request.urlopen(INSTALLER_URL, timeout=60) as response:
            installer.write_bytes(response.read())
    except OSError as error:
        raise CloudPanelError(f"Could not download the CloudPanel installer: {error}") from None

    actual = hashlib.sha256(installer.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise CloudPanelError(
            "The CloudPanel installer does not match its expected checksum, so it was not run.\n"
            f"  expected {expected_sha256}\n"
            f"  got      {actual}\n"
            f"If CloudPanel has published a new installer, check the hash on {INSTALLER_DOCS}\n"
            "and re-run with --installerSha256=<published hash>."
        )


def create_admin_user():
    """Create the admin with a generated password. Returns (url, password)."""
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
        raise CloudPanelError(f"Could not create admin user {ADMIN_USER}: {error}") from None

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
    except OSError as error:
        ui.warn(f"Could not save {ADMIN_FILE}: {error}. Copy the password shown at the end now.")
    return url, password


def write_private_file(path, content):
    """Write a file that only root can read, in a folder only root can open."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content)
    os.chmod(path, 0o600)
