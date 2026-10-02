"""github:key and github:key:rotate: the server's shared GitHub key.

    cloudpanel github:key          show it (creates it if there isn't one yet)
    cloudpanel github:key:rotate   replace it on every site that uses it

Rotate when the key may have leaked (e.g. a site was compromised) or just
periodically. Sites with a different key are left alone (site:fix:ssh
--apply moves them over).
"""

from pathlib import Path

from .. import db, ssh, ui
from ..errors import CloudPanelError


def _pub():
    return Path(f"{ssh.SHARED_GITHUB_KEY}.pub")


def show(params):
    created = ssh.ensure_shared_github_key()
    ssh.print_shared_key_instructions("New shared GitHub key for this server" if created else "Shared GitHub key for this server")
    ui.note(f"Fingerprint: {ssh.fingerprint(_pub())}")
    ui.note('Check from a site: ssh -T git@github.com  (should say "Hi <your username>!")')


def rotate(params):
    try:
        sites = [s for s in db.list_sites() if (ssh.HOME / s["user"]).is_dir()]
    except Exception as error:
        raise CloudPanelError(f"Could not read CloudPanel's site list: {error}") from None

    had_key = _pub().exists()
    old_fingerprint = ssh.fingerprint(_pub()) if had_key else None
    on_old_key = [s for s in sites if had_key and ssh.uses_shared_github_key(s["user"])]
    others = [s for s in sites if s not in on_old_key]

    # Replace the key; the old private key is deleted so it can't be reused.
    ssh.SHARED_GITHUB_KEY.unlink(missing_ok=True)
    _pub().unlink(missing_ok=True)
    ssh.ensure_shared_github_key()
    ui.success("New shared GitHub key created.")

    for site in on_old_key:
        try:
            ssh.install_shared_github_key(site["user"])
            ui.success(f"{ui.bold(site['domain'])} now uses the new key.")
        except (CloudPanelError, OSError) as error:
            ui.fail(f"{site['domain']}: {error}")

    if others:
        ui.info(f"{len(others)} site(s) weren't on the shared key and were left alone: {', '.join(s['domain'] for s in others)}")
        ui.note("To move them to the shared key: cloudpanel site:fix:ssh --apply")

    ssh.print_shared_key_instructions("Add the NEW key to GitHub")
    if old_fingerprint:
        print(f"Then delete the OLD key on GitHub (Settings > SSH and GPG keys), fingerprint {ui.bold(old_fingerprint)}.")
        print("Until the new key is added, pushes and pulls from these sites will be refused.")
