"""Let's Encrypt certificates through Cloudflare DNS, installed on sites."""

import re
from pathlib import Path

from . import ui
from .errors import CloudPanelError
from .run import clpctl, run

LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")
CLOUDFLARE_CREDENTIALS = Path("/root/.cloudflare/token")
NGINX_CERTIFICATES = Path("/etc/nginx/ssl-certificates")


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
