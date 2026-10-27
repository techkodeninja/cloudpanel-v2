# CloudPanel CLI (v2)

Tools and automation on top of CloudPanel's `clpctl`: sites with Let's Encrypt certificates through Cloudflare, WordPress/ClassicPress (including multisite), SSH and GitHub keys for site users, restores, and migrations with automatic backups.

This version is written in Python using only the standard library. A server needs nothing but `python3` (already on Ubuntu), so there's no nvm, no npm and no private key to download it.

It replaces the Node version ([techkodeninja/cloudpanel](https://github.com/techkodeninja/cloudpanel)); every command and option works the same.

## Install
On a fresh Ubuntu (22.04, 24.04 or 26.04) or Debian (12, 13) server, as root:
<pre>
curl -fsSL https://github.com/techkodeninja/cloudpanel-v2/releases/latest/download/cloudpanel -o /usr/local/bin/cloudpanel && chmod +x /usr/local/bin/cloudpanel && cloudpanel --version
</pre>
To update:
<pre>
cloudpanel self:update            # installs the latest release if it's newer
cloudpanel self:update --check    # only checks
</pre>
Servers only get published releases, not every change merged to `develop`. Commands also tell you (at most once a day) when a newer release is out. `CLOUDPANEL_NO_UPDATE_CHECK=1` turns that off.

Requires Python 3.10 or newer (`python3 --version`).

## Usage
Run `cloudpanel` to see every command. For example:
<pre>
cloudpanel init:install --cfToken='0123456789abcdef0123456789'
cloudpanel site:add:php --domainName='amicable.codestacks.cc'
cloudpanel site:fix:ssh --apply
cloudpanel github:key
cloudpanel cloudflare:token          # new token if the old one leaked
cloudpanel site:update:certificate --domainName='amicable.codestacks.cc'
cloudpanel site:update:certificates  # every site whose certificate is older than its Let's Encrypt one
cloudpanel site:migration:wordpress --domainName='codestacks.cc' --stagingName='staging.codestacks.cc'
</pre>

## Staging (WordPress and ClassicPress)
<pre>
cloudpanel site:staging:create --domainName='codestacks.cc'
cloudpanel site:migration:wordpress --domainName='codestacks.cc' --stagingName='staging.codestacks.cc' --filesOnly
</pre>
1. `site:staging:create` copies the live site's files and database to `staging.codestacks.cc`, creating that site the first time. Run it again any time for a fresh copy.
2. The copy is private: only people logged in to WordPress can see it (use your live site's login), search engines are told to skip it, and it never sends email.
3. Make your changes on staging, then push them live with `site:migration:wordpress`. The live site is backed up first.
   - `--filesOnly` copies only themes and plugins. The live database and uploads are kept, so orders, comments, sign-ups and images added since the copy stay.
   - Without it, the live database and all of `wp-content` are replaced with staging's. Only do this when nothing on the live site has changed since the copy.

Multisite networks aren't supported by `site:staging:create` yet.

## Files on the server
The tool keeps per-server settings on the server, never in this repo:

| File | What |
|---|---|
| `/root/.cloudflare/token` | Cloudflare API token (root-only) |
| `/root/.cloudflare/admin` | generated CloudPanel admin login (root-only) |
| `/root/.ssh/cloudpanel_github_ed25519` | shared GitHub key copied to sites |
| `/root/cloudpanel-backups/` | backups made before migrations |
| `/etc/letsencrypt/renewal-hooks/deploy/cloudpanel` | after Certbot renews a certificate, puts it on the sites that use it |

Certbot renews certificates on its own, but CloudPanel serves its own copy of each one. The hook above (installed by `init:install`, by adding a site, and by `site:update:certificates`) runs `cloudpanel site:update:certificates` after every renewal so sites get the new certificate.

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

Before committing a change to `cloudpanel/`, raise `__version__` in `cloudpanel/__init__.py` and rebuild the bundle so `dist/cloudpanel` matches:
<pre>
./build.sh
</pre>

To try a branch on a server before releasing it:
<pre>
cloudpanel self:update --branch=develop
</pre>

## Branches and releasing
All work goes into `develop` (the default branch) through pull requests. Servers only get what you publish as a release.

To release:
1. Raise `__version__` in `cloudpanel/__init__.py` (e.g. `0.0.3`) in a PR into `develop` and merge it.
2. On GitHub: **Releases → Draft a new release → Choose a tag**, type `v0.0.3` (a `v` plus the version), target `develop`, **Publish release**.
3. The release workflow checks the tag matches the version, runs the tests, and attaches the `cloudpanel` file. When it's done, servers see the update.
