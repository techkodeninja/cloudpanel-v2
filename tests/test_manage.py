import os
import sqlite3
from pathlib import Path
from unittest import mock

from cloudpanel import ssh
from tests.helpers import FakeCommands
from tests.test_sites import FAKES, LAPTOP, SiteTestCase


class ListTest(SiteTestCase):
    def test_lists_sites_with_type_and_certificate(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE site SET application = 'WordPress' WHERE domain_name IN ('example.com', 'api.example.com')")
            conn.execute("UPDATE site SET application = 'ReverseProxy' WHERE domain_name = 'a.b.example.com'")
        # api.example.com is ClassicPress (its version.php defines $cp_version)
        wp_includes = self.home / "example-api" / "htdocs" / "api.example.com" / "wp-includes"
        wp_includes.mkdir(parents=True)
        (wp_includes / "version.php").write_text("<?php\n$wp_version = '6.2';\n$cp_version = '2.6.0';\n")
        # example.com has a certificate, found through its nginx vhost
        nginx = self.tmp / "nginx"
        nginx.mkdir()
        cert = self.tmp / "example.com.crt"
        cert.write_text("x")
        (nginx / "example.com.conf").write_text(f"server {{\n  ssl_certificate {cert};\n  ssl_certificate_key /x.key;\n}}\n")

        with FakeCommands("openssl", output="notAfter=Dec 31 00:00:00 2026 GMT\n"):
            code, out, err = self.cli("site:list")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertIn("ClassicPress", next(l for l in lines if "api.example.com" in l))
        self.assertIn("12-31-2026", next(l for l in lines if " example.com " in l))
        self.assertIn("Reverse Proxy", next(l for l in lines if "a.b.example.com" in l))
        self.assertIn("Not found", next(l for l in lines if "a.b.example.com" in l))


class DeleteTest(SiteTestCase):
    def test_delete_by_domain_with_yes(self):
        with FakeCommands("clpctl") as fakes:
            code, out, err = self.cli("site:delete", "--domainName=api.example.com", "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(fakes.calls[0]["argv"], ["clpctl", "site:delete", "--domainName=api.example.com"])
        self.assertEqual(fakes.calls[0]["stdin"], "yes\n")
        self.assertIn("api.example.com has been deleted", out)

    def test_needs_typed_confirmation(self):
        with FakeCommands("clpctl") as fakes, mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("builtins.input", return_value="example.com"):
            code, out, _ = self.cli("site:delete", "--domainName=api.example.com")
        self.assertEqual(code, 1)
        self.assertIn("Cancelled; nothing was deleted", out)
        self.assertEqual(fakes.argvs, [])

    def test_interactive_pick_and_confirm(self):
        answers = iter(["2", "api.example.com"])
        with FakeCommands("clpctl") as fakes, mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("builtins.input", lambda prompt="": next(answers)):
            code, out, _ = self.cli("site:delete")
        self.assertEqual(code, 0)
        self.assertIn("api.example.com", out)
        self.assertEqual(fakes.argvs, [["clpctl", "site:delete", "--domainName=api.example.com"]])

    def test_without_terminal_or_yes_refuses(self):
        with FakeCommands("clpctl") as fakes:
            code, out, _ = self.cli("site:delete", "--domainName=api.example.com")
        self.assertEqual(code, 1)
        self.assertIn("Re-run with --yes", out)
        self.assertEqual(fakes.argvs, [])

    def test_unknown_domain(self):
        with FakeCommands("clpctl"):
            code, _, err = self.cli("site:delete", "--domainName=nope.com", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("nope.com was not found", err)

    def test_no_terminal_no_domain(self):
        with FakeCommands("clpctl"):
            code, _, err = self.cli("site:delete")
        self.assertEqual(code, 1)
        self.assertIn("Use --domainName", err)


class FixSshTest(SiteTestCase):
    def make_site_user(self, user, *, authorized="", key=None):
        directory = self.home / user / ".ssh"
        directory.mkdir(parents=True)
        (directory / "authorized_keys").write_text(authorized)
        if key:
            (directory / "id_ed25519").write_text(key)
            (directory / "id_ed25519.pub").write_text("ssh-ed25519 AAAAsomething x\n")

    def test_report_apply_rerun(self):
        # example: old version (root's key copied, no login key); example-api: fine except
        # no git identity and not on the shared key; example-b-a: no home folder (skipped)
        self.make_site_user("example", key="ROOT-PRIVATE-KEY\n")
        self.make_site_user("example-api", authorized=LAPTOP + "\nssh-rsa AAAAB3NzaC1yc2EAAAADAQABprovider== provider\n")

        with FakeCommands(*FAKES):
            code, out, _ = self.cli("site:fix:ssh")
        self.assertEqual(code, 0)
        self.assertIn("2 of 2 site(s) need fixing", out)
        self.assertIn("holds a copy of root's key", out)
        self.assertIn("missing your login key", out)
        self.assertIn("no git name/email", out)
        self.assertIn("not on the shared GitHub key", out)
        self.assertFalse((self.root_ssh / "cloudpanel_github_ed25519").exists())  # report changes nothing

        with FakeCommands(*FAKES):
            code, out, err = self.cli("site:fix:ssh", "--apply")
            self.assertEqual(code, 0, err + out)
            self.assertIn("2 site(s) fixed", out)
            shared = (self.root_ssh / "cloudpanel_github_ed25519").read_text()
            for user in ("example", "example-api"):
                self.assertEqual((self.home / user / ".ssh" / "id_ed25519").read_text(), shared)
                self.assertIn(LAPTOP, (self.home / user / ".ssh" / "authorized_keys").read_text())

            code, out, _ = self.cli("site:fix:ssh")  # same fake sudo store: identities persist
        self.assertEqual(code, 0)
        self.assertIn("All 2 site(s) are fine", out)

    def test_unknown_domain(self):
        code, _, err = self.cli("site:fix:ssh", "--domainName=nope.com")
        self.assertEqual(code, 1)
        self.assertIn("nope.com was not found", err)


class GithubKeyTest(SiteTestCase):
    def test_show_creates_then_shows(self):
        with FakeCommands("ssh-keygen"):
            _, first, _ = self.cli("github:key")
            _, second, _ = self.cli("github:key")
        self.assertIn("New shared GitHub key for this server", first)
        self.assertIn("Shared GitHub key for this server", second)
        self.assertNotIn("New shared", second)
        self.assertIn("Fingerprint: SHA256:", second)

    def test_rotate(self):
        with FakeCommands(*FAKES):
            self.cli("site:add:php", "--domainName=example.com")
            self.cli("site:add:php", "--domainName=api.example.com")
            # a third site with a hand-made key is left alone
            custom = self.home / "example-b-a" / ".ssh"
            custom.mkdir(parents=True)
            (custom / "id_ed25519").write_text("HANDMADE\n")
            (custom / "id_ed25519.pub").write_text("ssh-ed25519 AAAAhandmade c\n")
            old = (self.root_ssh / "cloudpanel_github_ed25519").read_text()

            code, out, err = self.cli("github:key:rotate")
        self.assertEqual(code, 0, err)
        new = (self.root_ssh / "cloudpanel_github_ed25519").read_text()
        self.assertNotEqual(old, new)
        self.assertEqual((self.home / "example" / ".ssh" / "id_ed25519").read_text(), new)
        self.assertEqual((self.home / "example-api" / ".ssh" / "id_ed25519").read_text(), new)
        self.assertEqual((custom / "id_ed25519").read_text(), "HANDMADE\n")
        self.assertIn("left alone: a.b.example.com", out)
        self.assertIn("delete the OLD key on GitHub", out)


if __name__ == "__main__":
    import unittest
    unittest.main()
