"""Small, careful edits to a site's nginx vhost."""

import re

from .run import run


def remove_www1(conf, domain):
    """CloudPanel adds www1.<domain> as a server name; drop it."""
    text = conf.read_text()
    tidy = text.replace(f"www1.{domain}", "")
    if tidy != text:
        conf.write_text(tidy)


def add_wildcard_server_name(conf, domain):
    """Make the vhost also answer for *.domain (subdomain multisite).

    Checks the config with `nginx -t` and reloads; if either fails, the
    original file is put back and the error is raised.
    """
    original = conf.read_text()
    wildcard = f"*.{domain}"
    changed = False

    def add(match):
        nonlocal changed
        names = match.group(2).split()
        if domain not in names or wildcard in names:
            return match.group(0)
        changed = True
        return f"{match.group(1)}{' '.join([*names, wildcard])};"

    updated = re.sub(r"^(\s*server_name\s+)([^;]*);", add, original, flags=re.M)
    if not changed:
        if wildcard in original:
            return
        raise ValueError(f"no server_name for {domain} found in {conf}")

    conf.write_text(updated)
    try:
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    except Exception:
        conf.write_text(original)
        try:
            run(["systemctl", "reload", "nginx"])
        except Exception:
            pass
        raise
