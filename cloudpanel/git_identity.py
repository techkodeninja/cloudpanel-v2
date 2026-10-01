"""Git author name/email for site users, copied from root.

git refuses to commit until user.name and user.email are set, and each site
is a new Linux user. This is only the author label in commits; pushing uses
the site's SSH key.
"""

from . import ui
from .errors import CloudPanelError
from .run import as_site_user, run

KEYS = ("user.name", "user.email")


def read_identity(user=None):
    """Global git user.name/email for root (user=None) or a site user."""
    identity = {}
    for key in KEYS:
        args = ["git", "config", "--global", "--get", key]
        try:
            value = (as_site_user(user, args) if user else run(args)).strip()
        except CloudPanelError:
            continue  # not set (git exits 1)
        if value:
            identity[key] = value
    return identity


def missing_identity(user, root=None):
    root = read_identity() if root is None else root
    site = read_identity(user)
    return [key for key in KEYS if root.get(key) and not site.get(key)]


def setup_identity(user):
    """Copy root's git name/email to a site user (keeps values it already has)."""
    root = read_identity()
    if not (root.get("user.name") and root.get("user.email")):
        ui.warn(f"Root has no git name/email, so {user} can't commit until they're set. "
                'Set them once as root (git config --global user.name "..." and user.email "..."), '
                "then run cloudpanel site:fix:ssh --apply.")
        return False
    try:
        for key in missing_identity(user, root):
            as_site_user(user, ["git", "config", "--global", key, root[key]])
    except CloudPanelError as error:
        ui.warn(f"Could not set git name/email for {user}: {error}")
        return False
    site = read_identity(user)
    ui.success(f"git commits as {ui.bold(f'{site.get(KEYS[0])} <{site.get(KEYS[1])}>')} for {user}")
    return True
