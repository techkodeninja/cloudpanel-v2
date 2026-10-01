"""nvm + Node LTS for site users (for sites with a JavaScript build step)."""

from pathlib import Path

from . import ui
from .errors import CloudPanelError
from .run import as_site_user

HOME = Path("/home")

# Same location nvm's installer uses: $XDG_CONFIG_HOME/nvm, else ~/.nvm.
# No "set -u": nvm.sh reads unset variables and would abort.
NVM_INSTALL = """
set -eo pipefail
NVM_DIR="${XDG_CONFIG_HOME:+$XDG_CONFIG_HOME/nvm}"; NVM_DIR="${NVM_DIR:-$HOME/.nvm}"; export NVM_DIR
mkdir -p "$NVM_DIR"
[ -s "$NVM_DIR/nvm.sh" ] || curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
. "$NVM_DIR/nvm.sh"
nvm install --lts
"""


def setup_node(user):
    """Install nvm + Node LTS for a site user. Skipped if ~/.npm exists. Never raises."""
    if (HOME / user / ".npm").exists():
        ui.info(f"npm is already set up for {ui.bold(user)}")
        return True
    try:
        as_site_user(user, ["bash", "-lc", NVM_INSTALL])
    except CloudPanelError as error:
        ui.warn(f"Could not install npm for {user}: {error}")
        return False
    ui.success(f"{ui.bold('npm')} has been installed.")
    return True
