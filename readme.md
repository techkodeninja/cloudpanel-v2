# CloudPanel CLI (v2)

Tools and automation on top of CloudPanel's `clpctl`: sites with Let's Encrypt certificates through Cloudflare, WordPress/ClassicPress (including multisite), SSH and GitHub keys for site users, restores, and migrations with automatic backups.

Version 2 is written in Python using only the standard library. A server needs nothing but `python3` (already on Ubuntu), so there's no nvm, no npm and no private key to download it.

It replaces the Node version ([techkodeninja/cloudpanel](https://github.com/techkodeninja/cloudpanel)); every command and option works the same.

## Install
On a fresh Ubuntu (22.04, 24.04 or 26.04) or Debian (12, 13) server, as root:
<pre>
curl -fsSL https://raw.githubusercontent.com/techkodeninja/cloudpanel-v2/main/dist/cloudpanel -o /usr/local/bin/cloudpanel
chmod +x /usr/local/bin/cloudpanel
cloudpanel --help
</pre>
To update:
<pre>
cloudpanel self:update            # updates if there's a newer version
cloudpanel self:update --check    # only checks
</pre>
Commands also tell you (at most once a day) when a newer version is out. `CLOUDPANEL_NO_UPDATE_CHECK=1` turns that off.

Requires Python 3.10 or newer (`python3 --version`).

## Usage
Run `cloudpanel` to see every command. For example:
<pre>
cloudpanel init:install --cfToken='0123456789abcdef0123456789'
cloudpanel site:add:php --domainName='amicable.codestacks.cc'
cloudpanel site:fix:ssh --apply
cloudpanel github:key
cloudpanel site:update:certificate --domainName='amicable.codestacks.cc'
cloudpanel site:migration:wordpress --domainName='codestacks.cc' --stagingName='staging.codestacks.cc'
</pre>

## Files on the server
The tool keeps per-server settings on the server, never in this repo:

| File | What |
|---|---|
| `/root/.cloudflare/token` | Cloudflare API token (root-only) |
| `/root/.cloudflare/admin` | generated CloudPanel admin login (root-only) |
| `/root/.ssh/cloudpanel_github_ed25519` | shared GitHub key copied to sites |
| `/root/cloudpanel-backups/` | backups made before migrations |

## Development
<pre>
cloudpanel/              the source (standard library only)
├── cli.py               argument parsing, help, command list
├── ui.py                ✔/⚠/✖ output and the type-to-confirm prompt
├── run.py               runs commands with argument lists (never a shell)
├── errors.py
└── commands/            one module per group of commands
tests/                   python3 -m unittest
├── fakes/recorder.py    stand-in for clpctl, wp, certbot, ... that records its arguments
build.sh                 bundles cloudpanel/ into dist/cloudpanel (one file)
dist/cloudpanel          what servers download
</pre>

Run from source and test:
<pre>
python3 -m cloudpanel --help
python3 -m unittest
</pre>

Before committing a change to `cloudpanel/`, raise `__version__` in `cloudpanel/__init__.py` (that's how servers know there's an update) and rebuild the bundle so `dist/cloudpanel` matches:
<pre>
./build.sh
</pre>
