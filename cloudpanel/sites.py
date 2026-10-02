"""Small shared helpers for site commands."""

from pathlib import Path

from .errors import UsageError

NGINX_SITES = Path("/etc/nginx/sites-enabled")
HOME = Path("/home")


def htdocs(user, domain):
    """Where a site's files live."""
    return HOME / user / "htdocs" / domain


def site_username(domain):
    """Linux user for a site: example.com -> example, blog.example.com -> example-blog."""
    parts = domain.split(".")
    if len(parts) > 2:
        return "-".join(list(reversed(parts))[1:])
    return parts[0]


def nginx_conf(domain):
    return NGINX_SITES / f"{domain}.conf"


def require(params, *names):
    """Return the values of required --options, or raise a clear error."""
    missing = [name for name in names if not isinstance(params.get(name), str) or not params[name].strip()]
    if missing:
        raise UsageError(f"Missing {', '.join('--' + m for m in missing)}. Run \"cloudpanel\" to see usage.")
    return [params[name].strip() for name in names]
