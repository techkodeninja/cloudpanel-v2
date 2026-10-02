"""site:fix:ssh: find and fix SSH/git problems on existing sites.

    cloudpanel site:fix:ssh [--domainName=example.com] [--apply]

Checks every site (or one) for:
  - a copy of root's private key (older versions copied it)
  - not using the server's shared GitHub key
  - missing your login key, so `ssh <siteuser>@server` is refused
  - no git name/email, so commits fail
Without --apply it only reports; with --apply it fixes each problem.
"""

from pathlib import Path

from .. import db, git_identity, ssh, ui
from ..errors import CloudPanelError, UsageError

PROBLEMS = {
    "root_copy": "holds a copy of root's key",
    "missing_login": "missing your login key",
    "missing_git": "no git name/email",
    "not_shared": "not on the shared GitHub key",
}


def check(site, login_keys, root_git):
    user = site["user"]
    problems = []
    if ssh.has_root_key_copy(user):
        problems.append("root_copy")
    if ssh.missing_login_keys(user, login_keys):
        problems.append("missing_login")
    if git_identity.missing_identity(user, root_git):
        problems.append("missing_git")
    if "root_copy" not in problems and not ssh.uses_shared_github_key(user):
        problems.append("not_shared")
    return problems


def fix(site, problems):
    user, domain = site["user"], site["domain"]
    if "missing_login" in problems or "root_copy" in problems:
        ssh.add_login_keys(user, domain)
    if "missing_git" in problems:
        git_identity.setup_identity(user)
    if "root_copy" in problems or "not_shared" in problems:
        ssh.setup_github_key(user)


def fix_ssh(params):
    domain = params.get("domainName")
    if domain is True:
        raise UsageError("--domainName needs a value: --domainName=example.com")
    apply = bool(params.get("apply"))

    try:
        sites = db.list_sites()
    except Exception as error:
        raise CloudPanelError(f"Could not read CloudPanel's site list: {error}") from None
    if domain:
        sites = [s for s in sites if s["domain"] == domain]
        if not sites:
            raise CloudPanelError(f"{domain} was not found in CloudPanel.")
    sites = [s for s in sites if (ssh.HOME / s["user"]).is_dir()]

    login_keys = ssh.read_login_keys()
    if not login_keys:
        ui.warn(f"No login keys found in {ssh.ROOT_PUBLIC_KEY} or {ssh.ROOT_AUTHORIZED_KEYS}.")
    root_git = git_identity.read_identity()
    if not (root_git.get("user.name") and root_git.get("user.email")):
        ui.warn("Root has no git name/email, so sites can't get one. Set them once as root:")
        print('  git config --global user.name "Your Name"')
        print('  git config --global user.email "you@users.noreply.github.com"')

    affected = [(site, problems) for site in sites if (problems := check(site, login_keys, root_git))]
    if not affected:
        ui.success(f"All {len(sites)} site(s) are fine: shared GitHub key, login key and git name/email in place; no copy of root's key.")
        return

    if not apply:
        ui.warn(f"{len(affected)} of {len(sites)} site(s) need fixing:")
        for site, problems in affected:
            print(f"  {ui.bold(site['domain'])} {ui.gray('(' + site['user'] + ': ' + ', '.join(PROBLEMS[p] for p in problems) + ')')}")
        print()
        print("To fix them, run:")
        print(f"  cloudpanel site:fix:ssh{f' --domainName={domain!r}' if domain else ''} --apply")
        if any({"root_copy", "not_shared"} & set(p) for _, p in affected):
            print()
            print("Those sites will use the server's shared GitHub key. Make sure it's on your GitHub account")
            print('("cloudpanel github:key" shows it) before pushing from them again.')
        return

    failed = []
    for site, problems in affected:
        ui.info(f"Fixing {ui.bold(site['domain'])}")
        fix(site, problems)
        left = check(site, login_keys, root_git)
        if left:
            failed.append(site["domain"])
            ui.fail(f"{site['domain']} still has: {', '.join(PROBLEMS[p] for p in left)}")

    if failed:
        ui.fail(f"{len(failed)} of {len(affected)} site(s) could not be fixed: {', '.join(failed)}")
    else:
        ui.success(f"{len(affected)} site(s) fixed.")
