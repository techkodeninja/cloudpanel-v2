"""panel:domain and init:install --panelDomain."""

import stat
import unittest
from unittest import mock

from cloudpanel.commands import init
from tests.helpers import FakeCommands
from tests import test_init
from tests.test_init import FAKE_SHA256
from tests.test_sites import FAKES, SiteTestCase

ADMIN = "# CloudPanel admin\nCP_URL=https://192.168.50.11:8443\nCP_ADMIN=cpwarren\nCP_PASSWORD=secret\n"


class PanelDomainTest(SiteTestCase):
    def setUp(self):
        super().setUp()
        cloudflare = self.tmp / "root" / ".cloudflare"
        cloudflare.mkdir(parents=True)
        self.admin = cloudflare / "admin"
        self.admin.write_text(ADMIN)
        for patch in (mock.patch.object(init, "ADMIN_FILE", self.admin),
                      mock.patch("cloudpanel.system.server_ip", lambda: "192.168.50.11")):
            patch.start()
            self.addCleanup(patch.stop)

    def test_creates_reverse_proxy_with_certificate(self):
        with FakeCommands(*FAKES) as fakes:
            code, out, err = self.cli("panel:domain", "--domainName=https://Panel.Example.com/")
        self.assertEqual(code, 0, err)
        create = fakes.argvs[0]
        self.assertEqual(create[:5], ["clpctl", "site:add:reverse-proxy", "--domainName=panel.example.com",
                                      "--reverseProxyUrl=https://127.0.0.1:8443", "--siteUser=example-panel"])
        certbot = next(a for a in fakes.argvs if a[0] == "certbot")
        self.assertEqual(certbot[certbot.index("--cert-name") + 1], "example.com")
        self.assertTrue(any(a[:3] == ["clpctl", "site:install:certificate", "--domainName=panel.example.com"]
                            for a in fakes.argvs))
        # Saved login points at the new address; everything else kept, still root-only.
        admin = self.admin.read_text()
        self.assertIn("CP_URL=https://panel.example.com\n", admin)
        self.assertNotIn(":8443", admin)
        self.assertIn("CP_PASSWORD=secret", admin)
        self.assertEqual(stat.S_IMODE(self.admin.stat().st_mode), 0o600)
        self.assertIn("panel.example.com -> 192.168.50.11", out)

    def test_repeat_only_refreshes_certificate(self):
        (self.tmp / "nginx").mkdir()
        (self.tmp / "nginx" / "panel.example.com.conf").write_text(
            "location / {\n  proxy_pass https://127.0.0.1:8443;\n}\n")
        with FakeCommands(*FAKES) as fakes:
            code, out, err = self.cli("panel:domain", "--domainName=panel.example.com")
        self.assertEqual(code, 0, err)
        self.assertIn("already points to CloudPanel", out)
        self.assertFalse(any(a[:2] == ["clpctl", "site:add:reverse-proxy"] for a in fakes.argvs))
        self.assertTrue(any(a[:2] == ["clpctl", "site:install:certificate"] for a in fakes.argvs))

    def test_other_site_with_that_name_is_left_alone(self):
        (self.tmp / "nginx").mkdir()
        (self.tmp / "nginx" / "example.com.conf").write_text("root /home/example/htdocs;\n")
        with FakeCommands(*FAKES) as fakes:
            code, _, err = self.cli("panel:domain", "--domainName=example.com")
        self.assertEqual(code, 1)
        self.assertIn("already a site", err)
        self.assertEqual(fakes.argvs, [])
        self.assertEqual(self.admin.read_text(), ADMIN)

    def test_failed_certificate_keeps_old_url(self):
        with FakeCommands("clpctl"), FakeCommands("certbot", exit_code=1):
            code, _, _ = self.cli("panel:domain", "--domainName=panel.example.com")
        self.assertEqual(code, 1)
        self.assertEqual(self.admin.read_text(), ADMIN)

    def test_missing_domain(self):
        code, _, err = self.cli("panel:domain")
        self.assertEqual(code, 1)
        self.assertIn("--domainName", err)


class InitPanelDomainTest(unittest.TestCase):
    # InitInstallTest's setup and runner, without running its tests again.
    tearDown, cli = test_init.InitInstallTest.tearDown, test_init.InitInstallTest.cli

    def setUp(self):
        test_init.InitInstallTest.setUp(self)
        nginx = self.tmp / "nginx"
        patch = mock.patch("cloudpanel.sites.NGINX_SITES", nginx)
        patch.start()
        self.addCleanup(patch.stop)
        live = self.tmp / "letsencrypt" / "live" / "example.com"
        live.mkdir(parents=True)
        for name in ("privkey.pem", "fullchain.pem"):
            (live / name).write_text("x")

    def test_fresh_server_with_panel_domain(self):
        with FakeCommands("snap") as fakes:
            code, out, err = self.cli("--cfToken=abc", f"--installerSha256={FAKE_SHA256}",
                                      "--panelDomain=panel.example.com")
        self.assertEqual(code, 0, err)
        self.assertTrue(any(a[:3] == ["clpctl", "site:add:reverse-proxy", "--domainName=panel.example.com"]
                            for a in fakes.argvs))
        self.assertIn("CP_URL=https://panel.example.com", init.ADMIN_FILE.read_text())

    def test_bad_panel_domain_stops_before_installing(self):
        with FakeCommands("snap") as fakes:
            code, _, err = self.cli("--cfToken=abc", "--panelDomain")
        self.assertEqual(code, 1)
        self.assertIn("--panelDomain needs a value", err)
        self.assertEqual(fakes.argvs, [])
