"""site:install:certificate and site:update:certificate.

    cloudpanel site:install:certificate --domainName=example.com
        Get the certificate (if there isn't one yet) and install it on the site.
    cloudpanel site:update:certificate --domainName=example.com
        If the site's certificate is missing or expires within 30 days, install
        the current Let's Encrypt one (certbot renews that one itself).
    cloudpanel site:update:certificates [--certName=example.com]
        Every site (or every site on one certificate) whose installed
        certificate is older than its Let's Encrypt one gets the new one.
        Certbot runs this itself after each renewal, through a hook in
        /etc/letsencrypt/renewal-hooks/deploy/ that this command installs.
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .. import certificates, db, ui
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
    certificates.ensure_renewal_hook()
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


def _install_from(domain, live):
    clpctl([
        "site:install:certificate",
        f"--domainName={domain}",
        f"--privateKey={live / 'privkey.pem'}",
        f"--certificate={live / 'fullchain.pem'}",
        f"--certificateChain={live / 'chain.pem'}",
    ])


def update_all(params):
    """site:update:certificates; also what certbot's deploy hook runs."""
    cert_name = params.get("certName")
    if cert_name is True:
        raise UsageError("--certName needs a value: --certName=example.com")
    if not cert_name and os.environ.get("RENEWED_LINEAGE"):
        cert_name = Path(os.environ["RENEWED_LINEAGE"]).name  # set by certbot

    certificates.ensure_renewal_hook()

    sites = db.list_sites()
    if cert_name:
        sites = [s for s in sites if certificates.certificate_for(s["domain"])[0] == cert_name]
        if not sites:
            ui.info(f"No CloudPanel site uses the {cert_name} certificate; nothing to do.")
            return

    updated, current, failed = [], [], []
    for site in sites:
        domain = site["domain"]
        name, _ = certificates.certificate_for(domain)
        live = certificates.LETSENCRYPT_LIVE / name
        if not (live / "fullchain.pem").exists():
            ui.note(f"{domain}: no Let's Encrypt certificate ({name}); skipped.")
            continue

        new_expiry = certificates.expiry_date(live / "fullchain.pem")
        if new_expiry is None:
            ui.fail(f"{domain}: can't read {live / 'fullchain.pem'}.")
            failed.append(domain)
            continue
        installed = certificates.find_site_certificate(domain)
        old_expiry = certificates.expiry_date(installed) if installed else None
        # Only ever move forward: a site whose certificate lasts as long or
        # longer (e.g. a Cloudflare origin certificate) is left alone.
        if old_expiry and old_expiry >= new_expiry:
            current.append(domain)
            continue

        try:
            _install_from(domain, live)
        except CloudPanelError as error:
            ui.fail(f"{domain}: could not install the renewed certificate: {error}")
            failed.append(domain)
            continue
        updated.append(domain)
        ui.success(f"{ui.bold(domain)} now has the certificate valid until {new_expiry.strftime('%Y-%m-%d')}.")

    summary = f"{len(updated)} updated, {len(current)} already current"
    if failed:
        ui.fail(f"{summary}, {len(failed)} failed: {', '.join(failed)}")
    else:
        ui.success(f"{summary}.")
