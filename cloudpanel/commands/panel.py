"""panel:domain: reach the CloudPanel admin on your own domain.

    cloudpanel panel:domain --domainName=panel.example.com

CloudPanel's documented way to do this with your own certificate: a
reverse-proxy site for the domain that forwards to the panel on
https://127.0.0.1:8443. The site gets its Let's Encrypt certificate through
Cloudflare like every other site, and the renewal hook keeps it current.
https://<server-ip>:8443 keeps working.

Running it again for the same domain only reinstalls the certificate and
updates the saved login, so it is safe to repeat.
"""

from .. import certificates, system, ui
from ..errors import CloudPanelError, UsageError
from ..run import clpctl
from ..sites import nginx_conf, require, site_username
from . import init
from .certificate import normalize_domain

PANEL_URL = "https://127.0.0.1:8443"


def domain(params):
    (value,) = require(params, "domainName")
    set_domain(_normalize(value))


def _normalize(value):
    name = normalize_domain(value)
    if not name or "." not in name:
        raise UsageError(f"Invalid domain: {value}")
    return name


def set_domain(name):
    """Put the panel on `name`. Reports each step; raises only on errors that
    leave nothing to do (the name already belongs to another site)."""
    conf = nginx_conf(name)
    if conf.exists():
        if PANEL_URL.split("//", 1)[1] not in _read(conf):
            raise CloudPanelError(f"{name} is already a site on this server. "
                                  "Pick another name, or remove that site first (cloudpanel site:delete).")
        ui.info(f"{ui.bold(name)} already points to CloudPanel; refreshing its certificate.")
    else:
        try:
            clpctl([
                "site:add:reverse-proxy",
                f"--domainName={name}",
                f"--reverseProxyUrl={PANEL_URL}",
                f"--siteUser={site_username(name)}",
                f"--siteUserPassword={system.generate_password()}",
            ])
        except CloudPanelError as error:
            raise CloudPanelError(f"Could not create {name}: {error}") from None
        ui.success(f"{ui.bold(name)} now forwards to CloudPanel.")

    if not certificates.install_site_certificate(name):
        ui.exit_code = 1
        return

    url = f"https://{name}"
    _save_url(url)
    ip = system.server_ip() or "<server-ip>"
    ui.success(f"CloudPanel is at {ui.bold(url)} (https://{ip}:8443 still works).")
    print(f"  Needs a DNS record in Cloudflare: {name} -> {ip} (A record; proxied or not).")


def _read(path):
    try:
        return path.read_text()
    except OSError:
        return ""


def _save_url(url):
    """Point CP_URL in the saved admin login at the new address."""
    path = init.ADMIN_FILE
    if not path.exists():
        return
    try:
        lines = path.read_text().splitlines()
        lines = [f"CP_URL={url}" if line.startswith("CP_URL=") else line for line in lines]
        if not any(line.startswith("CP_URL=") for line in lines):
            lines.append(f"CP_URL={url}")
        init.write_private_file(path, "\n".join(lines) + "\n")
    except OSError as error:
        ui.warn(f"Could not update {path}: {error}")
