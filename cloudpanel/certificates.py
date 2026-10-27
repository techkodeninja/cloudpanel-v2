"""Let's Encrypt certificates through Cloudflare DNS, installed on sites."""

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import ui
from .errors import CloudPanelError
from .run import clpctl, run

LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")
CLOUDFLARE_CREDENTIALS = Path("/root/.cloudflare/token")
NGINX_CERTIFICATES = Path("/etc/nginx/ssl-certificates")
DEFAULT_PROGRAM = "/usr/local/bin/cloudpanel"


def renewal_hook_path():
    """Certbot runs every script here after it renews a certificate."""
    return LETSENCRYPT_LIVE.parent / "renewal-hooks" / "deploy" / "cloudpanel"


def renewal_hook_script(program):
    return (
        "#!/bin/sh\n"
        "# Installed by cloudpanel. Certbot renews certificates in /etc/letsencrypt,\n"
        "# but CloudPanel serves its own copy; this puts the renewed certificate on\n"
        "# every site that uses it ($RENEWED_LINEAGE says which one was renewed).\n"
        f"CLOUDPANEL_NO_UPDATE_CHECK=1 exec {program} site:update:certificates\n"
    )


def _program():
    """Path of the cloudpanel program to call from the hook."""
    from .commands.self_update import installed_path
    return str(installed_path() or shutil.which("cloudpanel") or DEFAULT_PROGRAM)


def ensure_renewal_hook(program=None):
    """Install (or refresh) the certbot deploy hook. True if it was written.

    Never raises: a missing hook is reported, not a reason to stop."""
    path = renewal_hook_path()
    script = renewal_hook_script(program or _program())
    try:
        if path.is_file() and not path.is_symlink() and path.read_text() == script:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(".cloudpanel.tmp")
        tmp.write_text(script)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    except OSError as error:
        ui.warn(f"Could not install the certificate renewal hook ({path}): {error}. "
                "Renewed certificates won't reach sites until you run cloudpanel site:update:certificates.")
        return False
    ui.success(f"Renewed certificates now go onto sites automatically (hook: {path}).")
    return True


def certificate_for(domain):
    """Which certificate a site uses: (certbot name, [domains on it]).

    The main domain and one level below share the main domain's
    certificate, domain.com + *.domain.com (also all that Cloudflare's free
    proxy certificate covers). A wildcard covers one level only, so deeper
    sites get a certificate for their exact name:
        example.com, blog.example.com -> example.com: example.com, *.example.com
        a.b.example.com               -> a.b.example.com: a.b.example.com
    """
    parts = domain.split(".")
    if len(parts) > 3:
        return domain, [domain]
    main = ".".join(parts[-2:])
    return main, [main, f"*.{main}"]


def install_site_certificate(domain):
    """Get the site's certificate (if there isn't one yet) and install it.

    Reports each step; returns True on success. Never raises.
    """
    name, domains = certificate_for(domain)
    live = LETSENCRYPT_LIVE / name
    cert, key = live / "fullchain.pem", live / "privkey.pem"
    ensure_renewal_hook()

    if not cert.exists():
        args = [
            "certbot", "certonly",
            "--noninteractive",
            "--agree-tos",
            "--register-unsafely-without-email",
            "--dns-cloudflare",
            "--dns-cloudflare-credentials", str(CLOUDFLARE_CREDENTIALS),
            "--dns-cloudflare-propagation-seconds", "25",
            "--cert-name", name,
        ]
        for item in domains:
            args += ["-d", item]
        try:
            run(args)
            ui.success(f"Certificate generated for {ui.bold(', '.join(domains))}")
        except CloudPanelError as error:
            ui.warn(f"Could not generate a certificate for {name}: {error}")
            return False

    try:
        clpctl(["site:install:certificate", f"--domainName={domain}", f"--privateKey={key}", f"--certificate={cert}"])
        ui.success(f"Certificate for {ui.bold(domain)} has been installed.")
        return True
    except CloudPanelError as error:
        ui.warn(f"Could not install the certificate on {domain}: {error}")
        return False


def find_site_certificate(domain, vhost_dir=None, cert_dir=None):
    """Path of the certificate nginx serves for exactly this site, or None.

    Read from the site's vhost (ssl_certificate ...;), falling back to a file
    named exactly <domain>.crt/.cert/.pem. Never matches another site
    (blog.example.com is not example.com).
    """
    from . import sites  # the nginx folder, patchable in one place
    vhost_dir = vhost_dir or sites.NGINX_SITES
    cert_dir = cert_dir or NGINX_CERTIFICATES
    try:
        vhost = (vhost_dir / f"{domain}.conf").read_text()
        match = re.search(r"^\s*ssl_certificate\s+([^;\s]+)\s*;", vhost, re.M)
        if match and Path(match.group(1)).exists():
            return Path(match.group(1))
    except OSError:
        pass
    for ext in (".crt", ".cert", ".pem"):
        candidate = cert_dir / f"{domain}{ext}"
        if candidate.exists():
            return candidate
    return None


def expiry_date(path):
    """When the certificate at `path` expires (UTC datetime), or None if it
    is missing or unreadable."""
    try:
        output = run(["openssl", "x509", "-enddate", "-noout", "-in", path])
        expiry = datetime.strptime(output.strip().split("=", 1)[1], "%b %d %H:%M:%S %Y %Z")
    except (CloudPanelError, ValueError, IndexError):
        return None
    return expiry.replace(tzinfo=timezone.utc)
