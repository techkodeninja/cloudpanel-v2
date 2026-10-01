import io
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from cloudpanel import certificates, cli, db, node, sites, ssh, ui
from tests.helpers import FakeCommands

LAPTOP = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIlaptopKey ben@laptop"
PROVIDER = 'no-port-forwarding,command="echo \'Please login as ubuntu\'" ssh-rsa AAAAB3NzaC1yc2EAAAADAQABprovider== provider'
FAKES = ("clpctl", "certbot", "sudo", "chown", "ssh-keygen")


class SiteTestCase(unittest.TestCase):
    """Runs commands against a temporary server layout and stand-in tools."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root_ssh = self.tmp / "root" / ".ssh"
        self.root_ssh.mkdir(parents=True)
        (self.root_ssh / "id_ed25519.pub").write_text(LAPTOP + "\n")
        (self.root_ssh / "id_ed25519").write_text("ROOT-PRIVATE-KEY\n")
        (self.root_ssh / "authorized_keys").write_text(PROVIDER + "\n" + LAPTOP + " same-key-again\n")
        self.home = self.tmp / "home"
        self.home.mkdir()

        # CloudPanel's database with one row per site, like the real one.
        self.db_path = self.tmp / "db.sq3"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE site (id INTEGER PRIMARY KEY, domain_name TEXT, user TEXT, application TEXT, ssh_keys TEXT)")
            for domain, user in [("example.com", "example"), ("api.example.com", "example-api"), ("a.b.example.com", "example-b-a")]:
                conn.execute("INSERT INTO site (domain_name, user, application, ssh_keys) VALUES (?, ?, 'PHP', '')", (domain, user))

        # Root's git identity (the real git reads this).
        self.gitconfig = self.tmp / "root.gitconfig"
        self.gitconfig.write_text("[user]\n\tname = Benjamin Lu\n\temail = b@users.noreply.github.com\n")

        patches = [
            mock.patch.object(sites, "NGINX_SITES", self.tmp / "nginx"),
            mock.patch.object(certificates, "LETSENCRYPT_LIVE", self.tmp / "letsencrypt"),
            mock.patch.object(ssh, "ROOT_PUBLIC_KEY", self.root_ssh / "id_ed25519.pub"),
            mock.patch.object(ssh, "ROOT_PRIVATE_KEY", self.root_ssh / "id_ed25519"),
            mock.patch.object(ssh, "ROOT_AUTHORIZED_KEYS", self.root_ssh / "authorized_keys"),
            mock.patch.object(ssh, "SHARED_GITHUB_KEY", self.root_ssh / "cloudpanel_github_ed25519"),
            mock.patch.object(ssh, "HOME", self.home),
            mock.patch.object(node, "HOME", self.home),
            mock.patch.object(db, "DB_PATH", self.db_path),
            mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(self.gitconfig)}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *argv):
        ui.exit_code = 0
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def site_keys_in_db(self, domain):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT ssh_keys FROM site WHERE domain_name = ?", (domain,)).fetchone()[0]


class AddPhpTest(SiteTestCase):
    def test_creates_site_with_everything(self):
        with FakeCommands(*FAKES) as fakes:
            code, out, err = self.cli("site:add:php", "--domainName=api.example.com")
        self.assertEqual(code, 0, err)
        argvs = fakes.argvs

        create = argvs[0]
        self.assertEqual(create[:6], ["clpctl", "site:add:php", "--domainName=api.example.com", "--phpVersion=8.4",
                                      "--vhostTemplate=Generic", "--siteUser=example-api"])
        self.assertTrue(create[6].startswith("--siteUserPassword="))

        certbot = next(a for a in argvs if a[0] == "certbot")
        self.assertEqual(certbot[certbot.index("--cert-name") + 1], "example.com")
        self.assertEqual([certbot[i + 1] for i, a in enumerate(certbot) if a == "-d"], ["example.com", "*.example.com"])
        self.assertIn(["clpctl", "site:install:certificate", "--domainName=api.example.com",
                       f"--privateKey={self.tmp}/letsencrypt/example.com/privkey.pem",
                       f"--certificate={self.tmp}/letsencrypt/example.com/fullchain.pem"], argvs)

        # nvm/Node as the site user
        self.assertTrue(any(a[:6] == ["sudo", "-u", "example-api", "-H", "bash", "-lc"] for a in argvs))

        # Login keys: laptop key + provider key with its options stripped, no duplicate
        auth = (self.home / "example-api" / ".ssh" / "authorized_keys").read_text().splitlines()
        self.assertEqual(auth, [LAPTOP, "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABprovider== provider"])
        self.assertEqual(stat.S_IMODE((self.home / "example-api" / ".ssh" / "authorized_keys").stat().st_mode), 0o600)
        self.assertIn(LAPTOP, self.site_keys_in_db("api.example.com"))

        # Git identity copied as the site user
        self.assertIn(["sudo", "-u", "example-api", "-H", "git", "config", "--global", "user.name", "Benjamin Lu"], argvs)
        self.assertIn(["sudo", "-u", "example-api", "-H", "git", "config", "--global", "user.email", "b@users.noreply.github.com"], argvs)

        # Shared GitHub key: created once, copied in, NOT root's key, instructions printed
        site_key = (self.home / "example-api" / ".ssh" / "id_ed25519").read_text()
        self.assertEqual(site_key, (self.root_ssh / "cloudpanel_github_ed25519").read_text())
        self.assertNotIn("ROOT-PRIVATE-KEY", site_key)
        self.assertEqual(stat.S_IMODE((self.home / "example-api" / ".ssh" / "id_ed25519").stat().st_mode), 0o600)
        self.assertIn("github.com ssh-ed25519", (self.home / "example-api" / ".ssh" / "known_hosts").read_text())
        self.assertIn("New shared GitHub key for this server", out)

    def test_second_site_reuses_the_shared_key(self):
        with FakeCommands(*FAKES):
            self.cli("site:add:php", "--domainName=api.example.com")
        with FakeCommands(*FAKES) as fakes:
            code, out, _ = self.cli("site:add:php", "--domainName=example.com")
        self.assertEqual(code, 0)
        self.assertNotIn("New shared GitHub key", out)
        self.assertIn("Uses the shared GitHub key SHA256:", out)
        self.assertFalse(any(a[0] == "ssh-keygen" and "-f" in a for a in fakes.argvs))
        self.assertEqual((self.home / "example" / ".ssh" / "id_ed25519").read_text(),
                         (self.home / "example-api" / ".ssh" / "id_ed25519").read_text())

    def test_php_version_option(self):
        with FakeCommands(*FAKES) as fakes:
            self.cli("site:add:php", "--domainName=example.com", "--phpVersion=8.3")
        self.assertIn("--phpVersion=8.3", fakes.argvs[0])

    def test_existing_site_is_an_error(self):
        (self.tmp / "nginx").mkdir()
        (self.tmp / "nginx" / "example.com.conf").write_text("server {}")
        with FakeCommands(*FAKES) as fakes:
            code, _, err = self.cli("site:add:php", "--domainName=example.com")
        self.assertEqual(code, 1)
        self.assertIn("example.com already exists", err)
        self.assertEqual(fakes.argvs, [])

    def test_clpctl_failure_stops(self):
        with FakeCommands("clpctl", exit_code=1, error="Site already exists in database.") as fakes:
            code, _, err = self.cli("site:add:php", "--domainName=example.com")
        self.assertEqual(code, 1)
        self.assertIn("Could not create example.com", err)
        self.assertIn("Site already exists in database.", err)
        self.assertEqual(len(fakes.argvs), 1)  # nothing after the failed create

    def test_missing_domain(self):
        code, _, err = self.cli("site:add:php")
        self.assertEqual(code, 1)
        self.assertIn("Missing --domainName", err)

    def test_certificate_failure_sets_exit_code_but_continues(self):
        with FakeCommands("clpctl", "sudo", "chown", "ssh-keygen") as fakes, \
                mock.patch.object(certificates, "run", side_effect=certificates.CloudPanelError("certbot: DNS problem")):
            code, out, _ = self.cli("site:add:php", "--domainName=example.com")
        self.assertEqual(code, 1)
        self.assertIn("Could not generate a certificate for example.com: certbot: DNS problem", out)
        self.assertIn("Shared GitHub key installed", out)  # the rest still ran


class StaticAndProxyTest(SiteTestCase):
    def test_static_two_level_subdomain_gets_exact_certificate(self):
        with FakeCommands(*FAKES) as fakes:
            code, _, err = self.cli("site:add:static", "--domainName=a.b.example.com")
        self.assertEqual(code, 0, err)
        self.assertEqual(fakes.argvs[0][:4], ["clpctl", "site:add:static", "--domainName=a.b.example.com", "--siteUser=example-b-a"])
        certbot = next(a for a in fakes.argvs if a[0] == "certbot")
        self.assertEqual([certbot[i + 1] for i, a in enumerate(certbot) if a == "-d"], ["a.b.example.com"])
        self.assertFalse(any(a[0] == "sudo" for a in fakes.argvs))  # no SSH/git setup for static sites

    def test_reverse_proxy_url_kept_whole(self):
        with FakeCommands(*FAKES) as fakes:
            code, _, _ = self.cli("site:add:reverse-proxy", "--domainName=app.example.com",
                                  "--reverseProxyUrl=http://127.0.0.1:8000/?a=1&b=2")
        self.assertEqual(code, 0)
        self.assertIn("--reverseProxyUrl=http://127.0.0.1:8000/?a=1&b=2", fakes.argvs[0])

    def test_reverse_proxy_needs_url(self):
        code, _, err = self.cli("site:add:reverse-proxy", "--domainName=app.example.com")
        self.assertEqual(code, 1)
        self.assertIn("Missing --reverseProxyUrl", err)


class BuildingBlocksTest(SiteTestCase):
    def test_certificate_for(self):
        self.assertEqual(certificates.certificate_for("example.com"), ("example.com", ["example.com", "*.example.com"]))
        self.assertEqual(certificates.certificate_for("blog.example.com"), ("example.com", ["example.com", "*.example.com"]))
        self.assertEqual(certificates.certificate_for("a.b.example.com"), ("a.b.example.com", ["a.b.example.com"]))

    def test_site_username(self):
        self.assertEqual(sites.site_username("example.com"), "example")
        self.assertEqual(sites.site_username("novarisframework.codestacks.cc"), "codestacks-novarisframework")
        self.assertEqual(sites.site_username("a.b.example.com"), "example-b-a")

    def test_root_key_copy_detection(self):
        (self.home / "old" / ".ssh").mkdir(parents=True)
        (self.home / "old" / ".ssh" / "id_ed25519").write_text("ROOT-PRIVATE-KEY\n")
        self.assertTrue(ssh.has_root_key_copy("old"))
        (self.home / "new" / ".ssh").mkdir(parents=True)
        (self.home / "new" / ".ssh" / "id_ed25519").write_text("SOMETHING-ELSE\n")
        self.assertFalse(ssh.has_root_key_copy("new"))

    def test_find_site_certificate_never_matches_another_site(self):
        cert_dir = self.tmp / "certs"
        cert_dir.mkdir()
        (cert_dir / "blog.example.com.crt").write_text("x")
        self.assertIsNone(certificates.find_site_certificate("example.com", self.tmp / "nginx", cert_dir))
        self.assertEqual(certificates.find_site_certificate("blog.example.com", self.tmp / "nginx", cert_dir),
                         cert_dir / "blog.example.com.crt")


if __name__ == "__main__":
    unittest.main()
