"""site:install:certificate and site:update:certificate.

    cloudpanel site:install:certificate --domainName=example.com
        Get the certificate (if there isn't one yet) and install it on the site.
    cloudpanel site:update:certificate --domainName=example.com
        If the site's certificate is missing or expires within 30 days, install
        the current Let's Encrypt one (certbot renews that one itself).
"""

import re
from datetime import datetime, timezone

from .. import certificates, ui
from ..errors import CloudPanelError, UsageError
from ..run import clpctl
from ..sites import require

RENEW_WITHIN_DAYS = 30


def normalize_domain(value):
    """'https://Example.com/path' -> 'example.com'."""
    domain = re.sub(r"^https?://", "", value.strip().lower())
    return domain.split("/", 1)[0]


def _domain(params):
    (value,) = require(params, "domainName")
    domain = normalize_domain(value)
    if not domain:
        raise UsageError("Invalid --domainName.")
    return domain


def status(path):
    """(needs_update, reason) for the certificate at path."""
    if not path or not path.exists():
        return True, "missing"
    expiry = certificates.expiry_date(path)
    if not expiry:
        return True, "unreadable"
    days = (expiry - datetime.now(timezone.utc)).days
    if days < 0:
        return True, f"expired {-days} day(s) ago"
    if days <= RENEW_WITHIN_DAYS:
        return True, f"expires in {days} day(s)"
    return False, f"valid for {days} more day(s)"


def install(params):
    domain = _domain(params)
    if not certificates.install_site_certificate(domain):
        ui.exit_code = 1


def update(params):
    domain = _domain(params)
    name, _ = certificates.certificate_for(domain)
    live = certificates.LETSENCRYPT_LIVE / name
    key, cert, chain = live / "privkey.pem", live / "fullchain.pem", live / "chain.pem"

    needs, reason = status(cert)
    if needs:
        raise CloudPanelError(
            f"The Let's Encrypt certificate for {name} needs renewing first ({reason}). "
            f"Run: certbot renew --cert-name {name}"
        )

    needs, reason = status(certificates.find_site_certificate(domain))
    if not needs:
        ui.success(f"No update needed for {ui.bold(domain)} ({reason}).")
        return

    ui.info(f"{domain}: certificate {reason}; installing the current one from {name}.")
    for path in (key, cert, chain):
        if not path.exists():
            raise CloudPanelError(f"Missing {path}")
    try:
        clpctl([
            "site:install:certificate",
            f"--domainName={domain}",
            f"--privateKey={key}",
            f"--certificate={cert}",
            f"--certificateChain={chain}",
        ])
    except CloudPanelError as error:
        raise CloudPanelError(f"Could not install the certificate on {domain}: {error}") from None
    ui.success(f"Certificate for {ui.bold(domain)} has been updated.")
