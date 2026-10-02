"""site:list and site:delete."""

import re
import sys
from datetime import datetime, timezone

from .. import certificates, db, ui
from ..errors import CloudPanelError, UsageError
from ..run import clpctl, command_exists, run
from ..sites import htdocs

APP_NAMES = {"ReverseProxy": "Reverse Proxy"}


def _require_cloudpanel():
    if not command_exists("clpctl"):
        raise CloudPanelError("CloudPanel isn't installed. Install it with: cloudpanel init:install --cfToken='...'")


def site_type(site):
    """CloudPanel's application type, with ClassicPress told apart from WordPress."""
    app = site["application"] or ""
    if app == "WordPress":
        try:
            version_php = (htdocs(site["user"], site["domain"]) / "wp-includes" / "version.php").read_text()
            if re.search(r"\$cp_version\s*=", version_php):
                return "ClassicPress"
        except OSError:
            pass
    return APP_NAMES.get(app, app)


def certificate_expiry(domain):
    """'MM-DD-YYYY' (UTC) for the certificate nginx serves this site, 'Not found' or 'Error'."""
    path = certificates.find_site_certificate(domain)
    if not path:
        return "Not found"
    try:
        output = run(["openssl", "x509", "-enddate", "-noout", "-in", path])
        expiry = datetime.strptime(output.strip().split("=", 1)[1], "%b %d %H:%M:%S %Y %Z")
        return expiry.replace(tzinfo=timezone.utc).strftime("%m-%d-%Y")
    except (CloudPanelError, ValueError, IndexError):
        return "Error"


def table(headers, rows):
    """Plain text table."""
    widths = [max(len(str(value)) for value in column) for column in zip(headers, *rows)]
    line = "+".join("-" * (width + 2) for width in widths)
    fmt = lambda values: "|".join(f" {str(value):<{width}} " for value, width in zip(values, widths))
    return "\n".join([fmt(headers), line, *(fmt(row) for row in rows)])


def list_sites(params):
    """cloudpanel site:list"""
    sites = db.list_sites()
    if not sites:
        print("No sites yet.")
        return
    rows = [[s["id"], s["domain"], s["user"], site_type(s), certificate_expiry(s["domain"])] for s in sites]
    print(table(["ID", "Domain Name", "Site User", "Type", "Cert Expiry"], rows))


def delete_site(params):
    """cloudpanel site:delete [--domainName=example.com] [--yes]"""
    _require_cloudpanel()
    sites = db.list_sites()
    if not sites:
        print("No sites to delete.")
        return

    domain = params.get("domainName")
    if domain is True:
        raise UsageError("--domainName needs a value: --domainName=example.com")

    if domain:
        if not any(s["domain"] == domain for s in sites):
            raise CloudPanelError(f"{domain} was not found in CloudPanel.")
    else:
        if not sys.stdin.isatty():
            raise UsageError("No terminal to choose a site. Use --domainName=example.com (and --yes to skip the question).")
        print(table(["ID", "Domain Name"], [[s["id"], s["domain"]] for s in sites]))
        choice = input("Enter the ID of the site to delete: ").strip()
        site = next((s for s in sites if str(s["id"]) == choice), None)
        if not site:
            print("No site with that ID; nothing was deleted.")
            return
        domain = site["domain"]

    if not params.get("yes"):
        print(f"This permanently deletes {ui.bold(domain)}: its files, databases and site user.")
        if not ui.confirm_by_typing(f"Type {domain} to delete it: ", domain):
            print("Cancelled; nothing was deleted." if sys.stdin.isatty()
                  else "Cancelled: no terminal to confirm. Re-run with --yes to skip the question.")
            ui.exit_code = 1
            return

    try:
        clpctl(["site:delete", f"--domainName={domain}"], input="yes\n")
    except CloudPanelError as error:
        raise CloudPanelError(f"Could not delete {domain}: {error}") from None
    ui.success(f"{ui.bold(domain)} has been deleted.")
