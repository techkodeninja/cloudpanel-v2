"""Command line: parse arguments, show help, run the chosen command."""

import importlib
import sys

from . import MIN_PYTHON, __version__, ui
from .errors import CloudPanelError

# command -> "module:function" in cloudpanel.commands, or None while it is
# still being ported from the Node version.
COMMANDS = {
    "init:install": "init:install",
    "site:add:classicpress": "wordpress:add_classicpress",
    "site:add:php": "sites:add_php",
    "site:add:reverse-proxy": "sites:add_reverse_proxy",
    "site:add:static": "sites:add_static",
    "site:add:wordpress": "wordpress:add_wordpress",
    "site:delete": "manage:delete_site",
    "site:list": "manage:list_sites",
    "site:fix:ssh": "fix_ssh:fix_ssh",
    "github:key": "github_key:show",
    "github:key:rotate": "github_key:rotate",
    "site:install:certificate": "certificate:install",
    "site:update:certificate": "certificate:update",
    "site:migration:classicpress": "migrate:wordpress",
    "site:migration:novaris": "migrate:novaris",
    "site:migration:wordpress": "migrate:wordpress",
    "site:restore:classicpress": "restore:restore_classicpress",
    "site:restore:wordpress": "restore:restore_wordpress",
    "self:update": "self_update:self_update",
}


def parse_args(args):
    """Turn ['--key=value', '--flag'] into {'key': 'value', 'flag': True}.

    Splits at the first '=' only, so values may contain '=' (URLs with
    ?a=b, tokens, passwords). Values stay strings. Anything not starting
    with '--' is ignored.
    """
    params = {}
    for arg in args:
        if not arg.startswith("--"):
            continue
        key, sep, value = arg[2:].partition("=")
        params[key] = value if sep else True
    return params


def help_text():
    h, note = ui.yellow, ui.gray
    ssh_report = note("(show sites with SSH/git problems)")
    github_show = note("(show this server's shared GitHub key)")
    version_line = note(f"cloudpanel {__version__}")
    cert_note = note("  update: reinstalls the Let's Encrypt certificate if the site's is missing or expires within 30 days.")
    return f"""
{h('main')}
cloudpanel init:install --cfToken='0123456789abcdef0123456789'
{note('  Creates admin cpwarren with a generated password, saved to /root/.cloudflare/admin.')}
{note('  Optional: --installerSha256=<hash> if CloudPanel has published a new installer.')}

{h('sites')}
cloudpanel site:add:classicpress --domainName='domain.com' --cpAdmin='john' --cpPassword='1234567890' --cpEmail='john@domain.com' --cpType='single'
cloudpanel site:add:php --domainName='domain.com'
cloudpanel site:add:reverse-proxy --domainName='domain.com' --reverseProxyUrl='https://127.0.0.1:8000'
cloudpanel site:add:static --domainName='domain.com'
cloudpanel site:add:wordpress --domainName='domain.com' --wpAdmin='john' --wpPassword='1234567890' --wpEmail='john@domain.com' --wpType='single'
cloudpanel site:delete [--domainName='domain.com'] [--yes]
cloudpanel site:list
{note('  --wpType / --cpType: single (default), subdirectory or subdomain (multisite).')}
{note('  subdomain needs the main domain (example.com) and a * DNS record in Cloudflare.')}
{note('  --phpVersion=8.4: WordPress, ClassicPress and restores default to 8.3; php sites to 8.4.')}

{h('ssh')}
cloudpanel site:fix:ssh                                   {ssh_report}
cloudpanel site:fix:ssh --apply                           {note('(fix them)')}
cloudpanel site:fix:ssh --domainName='domain.com' --apply

{h('github')}
cloudpanel github:key                                     {github_show}
cloudpanel github:key:rotate                              {note('(replace it with a new key on every site)')}

{h('certificate')}
cloudpanel site:install:certificate --domainName='domain.com'
cloudpanel site:update:certificate --domainName='domain.com'
{cert_note}

{h('migration')}
cloudpanel site:migration:classicpress --domainName='domain.com' --stagingName='staging.domain.com'
cloudpanel site:migration:novaris --domainName='domain.com' --stagingName='staging.domain.com'
cloudpanel site:migration:wordpress --domainName='domain.com' --stagingName='staging.domain.com'
{note('  Backs up domain.com to /root/cloudpanel-backups/ and asks you to type the domain first.')}
{note('  --yes skips the question (for scripts).')}

{h('restore')}
cloudpanel site:restore:classicpress --domainName='domain.com' --backupFile='https://downloads.domain.com/backup.tar'
cloudpanel site:restore:wordpress --domainName='domain.com' --backupFile='https://downloads.domain.com/backup.tar'

{h('update')}
cloudpanel self:update                                    {note('(download the latest version of this tool)')}

{version_line}
"""


def main(argv):
    """Run the CLI with argv (without the program name). Returns the exit code."""
    if sys.version_info < MIN_PYTHON:
        needed = ".".join(map(str, MIN_PYTHON))
        print(f"cloudpanel needs Python {needed} or newer (this is {sys.version.split()[0]}).", file=sys.stderr)
        return 1

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(help_text())
        return 0
    if argv[0] in ("-V", "--version", "version"):
        print(f"cloudpanel {__version__}")
        return 0

    command, params = argv[0], parse_args(argv[1:])

    if command not in COMMANDS:
        ui.fail(f"Unknown command: {command}")
        print(help_text())
        return 1

    target = COMMANDS[command]
    if target is None:
        ui.fail(f"{command} isn't available in this version yet. Use the Node version (techkodeninja/cloudpanel) for now.")
        return 2

    module_name, function_name = target.split(":")
    function = getattr(importlib.import_module(f"cloudpanel.commands.{module_name}"), function_name)

    try:
        function(params)
    except CloudPanelError as error:
        ui.fail(str(error))
        return 1
    except KeyboardInterrupt:
        print()
        ui.fail("Interrupted.")
        return 130

    return ui.exit_code


def run():
    """Entry point for `python3 -m cloudpanel` and the bundled file."""
    sys.exit(main(sys.argv[1:]))
