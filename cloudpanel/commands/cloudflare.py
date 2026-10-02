"""cloudflare:token — replace the saved Cloudflare API token.

    cloudpanel cloudflare:token                  asks for the new token (hidden)
    cloudpanel cloudflare:token --cfToken='...'  for scripts

Checks the new token with Cloudflare first and only saves it if it works,
so a typo never breaks certificates. Certbot reads the token from the file
each time, so new and renewed certificates use the new one right away.
"""

import getpass
import json
import sys
import urllib.error
import urllib.request

from .. import certificates, ui
from ..errors import CloudPanelError, UsageError
from .init import write_private_file

ZONES_URL = "https://api.cloudflare.com/client/v4/zones?per_page=50"


def check_token(token):
    """Names of the zones (domains) the token can see. Raises if it doesn't work."""
    request = urllib.request.Request(ZONES_URL, headers={"Authorization": f"Bearer {token}", "User-Agent": "cloudpanel"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            messages = "; ".join(e.get("message", "") for e in json.loads(error.read()).get("errors", []))
        except (ValueError, AttributeError):
            messages = ""
        raise CloudPanelError(f"Cloudflare rejected the token ({error.code}{': ' + messages if messages else ''}). Nothing was changed.") from None
    except (OSError, ValueError) as error:
        raise CloudPanelError(f"Could not reach Cloudflare to check the token: {error}. Nothing was changed.") from None
    if not data.get("success"):
        raise CloudPanelError("Cloudflare rejected the token. Nothing was changed.")
    zones = [zone.get("name", "") for zone in data.get("result", [])]
    if not zones:
        raise CloudPanelError("The token works but can't see any domains: give it Zone:Read and DNS:Edit. Nothing was changed.")
    return zones


def token(params):
    value = params.get("cfToken")
    if not isinstance(value, str):
        if not sys.stdin.isatty():
            raise UsageError("No terminal to ask for the token. Use --cfToken='...'.")
        value = getpass.getpass("New Cloudflare API token (hidden): ")
    value = value.strip()
    if not value:
        raise UsageError("The token is empty. Nothing was changed.")

    zones = check_token(value)
    ui.success(f"Token works; it can see: {', '.join(zones)}")

    path = certificates.CLOUDFLARE_CREDENTIALS
    write_private_file(path, f"# Cloudflare API token used by Certbot\ndns_cloudflare_api_token = {value}\n")
    ui.success(f"Saved to {path} (root-only). New and renewed certificates use it from now on.")
    print()
    print(ui.bold("If the old token was leaked: delete it in Cloudflare"))
    print("dash.cloudflare.com → My Profile → API Tokens → old token → ⋯ → Delete.")
    print("Saving a new one here doesn't switch the old one off.")
