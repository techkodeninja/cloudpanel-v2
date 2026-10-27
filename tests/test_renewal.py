"""Renewed certificates reach the sites: the certbot hook and site:update:certificates."""

import os
import shutil
import stat
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from cloudpanel import certificates
from tests.helpers import FakeCommands
from tests.test_sites import FAKES, SiteTestCase


def days(n):
    return datetime.now(timezone.utc) + timedelta(days=n)


class RenewalTest(SiteTestCase):
    """Sites in the test database: example.com and api.example.com share the
    example.com certificate; a.b.example.com has its own."""

    def setUp(self):
        super().setUp()
        (self.tmp / "nginx").mkdir()
        self.expiry = {}  # path -> expiry date, instead of running openssl
        patch = mock.patch.object(certificates, "expiry_date", lambda path: self.expiry.get(str(path)))
        patch.start()
        self.addCleanup(patch.stop)

    def live(self, name, expires_in):
        live = self.tmp / "letsencrypt" / name
        live.mkdir(parents=True, exist_ok=True)
        for file in ("privkey.pem", "fullchain.pem", "chain.pem"):
            (live / file).write_text("x")
        self.expiry[str(live / "fullchain.pem")] = days(expires_in)
        return live

    def installed(self, domain, expires_in):
        cert = self.tmp / f"{domain}.crt"
        cert.write_text("x")
        (self.tmp / "nginx" / f"{domain}.conf").write_text(f"server {{\n  ssl_certificate {cert};\n}}\n")
        self.expiry[str(cert)] = days(expires_in)

    def installs(self, fakes):
        return [a[2].removeprefix("--domainName=") for a in fakes.argvs if a[:2] == ["clpctl", "site:install:certificate"]]

    def hook(self):
        return certificates.renewal_hook_path()

    # --- what certbot's hook does ---------------------------------------------

    def test_renewal_goes_onto_every_site_using_that_certificate(self):
        live = self.live("example.com", 89)
        self.live("a.b.example.com", 89)
        for domain in ("example.com", "api.example.com", "a.b.example.com"):
            self.installed(domain, 5)
        with FakeCommands("clpctl") as fakes, mock.patch.dict(os.environ, {"RENEWED_LINEAGE": str(live)}):
            code, out, err = self.cli("site:update:certificates")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.installs(fakes), ["example.com", "api.example.com"])  # not a.b.example.com
        self.assertIn(f"--certificateChain={live}/chain.pem", fakes.argvs[0])
        self.assertIn("2 updated, 0 already current", out)

    def test_without_a_name_checks_every_site(self):
        self.live("example.com", 89)
        self.live("a.b.example.com", 89)
        self.installed("example.com", 89)        # already has the current one
        self.installed("api.example.com", 5)     # stale
        # a.b.example.com has none installed yet
        with FakeCommands("clpctl") as fakes:
            code, out, err = self.cli("site:update:certificates")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.installs(fakes), ["api.example.com", "a.b.example.com"])
        self.assertIn("2 updated, 1 already current", out)

    def test_longer_certificate_is_left_alone(self):
        self.live("example.com", 89)
        self.installed("example.com", 15 * 365)  # e.g. a Cloudflare origin certificate
        with FakeCommands("clpctl") as fakes:
            code, out, _ = self.cli("site:update:certificates", "--certName=example.com")
        self.assertEqual(code, 0)
        self.assertNotIn("example.com", self.installs(fakes))

    def test_unknown_certificate_does_nothing(self):
        with FakeCommands("clpctl") as fakes:
            code, out, _ = self.cli("site:update:certificates", "--certName=other.org")
        self.assertEqual(code, 0)
        self.assertIn("No CloudPanel site uses the other.org certificate", out)
        self.assertEqual(fakes.argvs, [])

    def test_failure_on_one_site_still_does_the_rest(self):
        self.live("example.com", 89)
        with FakeCommands("clpctl", exit_code=1, error="Site not found") as fakes:
            code, out, err = self.cli("site:update:certificates", "--certName=example.com")
        self.assertEqual(code, 1)
        self.assertEqual(self.installs(fakes), ["example.com", "api.example.com"])
        self.assertIn("2 failed: example.com, api.example.com", err)

    # --- the hook file ----------------------------------------------------------

    def test_hook_is_installed_once(self):
        self.assertTrue(certificates.ensure_renewal_hook(program="/usr/local/bin/cloudpanel"))
        self.assertFalse(certificates.ensure_renewal_hook(program="/usr/local/bin/cloudpanel"))
        self.assertEqual(self.hook(), self.tmp / "renewal-hooks" / "deploy" / "cloudpanel")
        self.assertEqual(stat.S_IMODE(self.hook().stat().st_mode), 0o755)
        self.assertIn("exec /usr/local/bin/cloudpanel site:update:certificates", self.hook().read_text())

    def test_hook_runs_the_command(self):
        with FakeCommands("cloudpanel") as fakes:
            certificates.ensure_renewal_hook(program=shutil.which("cloudpanel"))
            subprocess.run([str(self.hook())], check=True, stdin=subprocess.DEVNULL, env={**os.environ, "RENEWED_LINEAGE": "/x/example.com"})
        self.assertEqual(fakes.argvs, [["cloudpanel", "site:update:certificates"]])

    def test_adding_a_site_installs_the_hook(self):
        self.live("example.com", 89)
        with FakeCommands(*FAKES):
            code, _, err = self.cli("site:install:certificate", "--domainName=example.com")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.hook().is_file())


if __name__ == "__main__":
    unittest.main()
