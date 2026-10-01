import hashlib
import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from cloudpanel import cli, ui
from cloudpanel.commands import init
from tests.helpers import FakeCommands

# A stand-in CloudPanel installer: "installs" CloudPanel by putting a
# recording clpctl next to the other fake commands on PATH.
FAKE_INSTALLER = b"""#!/bin/bash
bin_dir="$(dirname "$(command -v snap)")"
ln -s "$(readlink -f "$bin_dir/snap")" "$bin_dir/clpctl"
echo "fake CloudPanel installer ran"
"""
FAKE_SHA256 = hashlib.sha256(FAKE_INSTALLER).hexdigest()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class InitInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cloudflare = self.tmp / "root" / ".cloudflare"
        patches = [
            mock.patch.object(init, "CLOUDFLARE_DIR", cloudflare),
            mock.patch.object(init, "TOKEN_FILE", cloudflare / "token"),
            mock.patch.object(init, "ADMIN_FILE", cloudflare / "admin"),
            mock.patch.object(init, "CERTBOT", self.tmp / "usr-bin-certbot"),
            mock.patch("urllib.request.urlopen", lambda *a, **k: FakeResponse(FAKE_INSTALLER)),
            mock.patch("cloudpanel.system.server_ip", lambda: "192.168.50.11"),
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
            code = cli.main(["init:install", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_fresh_server(self):
        with FakeCommands("snap") as fakes:
            code, out, err = self.cli("--cfToken=abc=123_-XYZ", f"--installerSha256={FAKE_SHA256}")

        self.assertEqual(code, 0, err)
        argvs = fakes.argvs
        # Certbot + plugin via snap
        self.assertIn(["snap", "install", "--classic", "certbot"], argvs)
        self.assertIn(["snap", "install", "certbot-dns-cloudflare"], argvs)
        self.assertTrue(init.CERTBOT.is_symlink())
        # Token: root-only, value kept whole (including '=')
        self.assertEqual(stat.S_IMODE(init.TOKEN_FILE.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(init.CLOUDFLARE_DIR.stat().st_mode), 0o700)
        self.assertIn("dns_cloudflare_api_token = abc=123_-XYZ", init.TOKEN_FILE.read_text())
        # Installer ran (it created clpctl), then the admin user was created
        # with a 24-char password. The installer's own output goes straight
        # to the terminal, so it isn't in `out`.
        self.assertIn("CloudPanel installation completed.", out)
        user_add = [a for a in argvs if a[:2] == ["clpctl", "user:add"]][0]
        self.assertIn("--userName=cpwarren", user_add)
        password = [a for a in user_add if a.startswith("--password=")][0].split("=", 1)[1]
        self.assertEqual(len(password), 24)
        # Login saved (root-only) and printed once, with the real IP
        admin = init.ADMIN_FILE.read_text()
        self.assertIn(f"CP_PASSWORD={password}", admin)
        self.assertIn("CP_URL=https://192.168.50.11:8443", admin)
        self.assertEqual(stat.S_IMODE(init.ADMIN_FILE.stat().st_mode), 0o600)
        self.assertIn(f"Password: {password}", out)

    def test_checksum_mismatch_does_not_run_installer(self):
        with FakeCommands("snap") as fakes:
            code, out, err = self.cli("--cfToken=abc")
        self.assertEqual(code, 1)
        self.assertIn("does not match its expected checksum", err)
        self.assertIn(f"got      {FAKE_SHA256}", out)
        self.assertNotIn("installation completed", out)
        self.assertFalse(any(a[0] == "clpctl" for a in fakes.argvs))
        self.assertFalse(init.ADMIN_FILE.exists())

    def test_already_installed_skips_installer(self):
        with FakeCommands("snap", "clpctl") as fakes:
            code, out, _ = self.cli("--cfToken=abc", f"--installerSha256={FAKE_SHA256}")
        self.assertEqual(code, 0)
        self.assertIn("already installed", out)
        self.assertFalse(any(a[:2] == ["clpctl", "user:add"] for a in fakes.argvs))

    def test_existing_token_is_kept(self):
        init.CLOUDFLARE_DIR.mkdir(parents=True)
        init.TOKEN_FILE.write_text("dns_cloudflare_api_token = original\n")
        with FakeCommands("snap", "clpctl"):
            _, out, _ = self.cli("--cfToken=new")
        self.assertIn("original", init.TOKEN_FILE.read_text())
        self.assertIn("was not saved", out)

    def test_no_token_warns(self):
        with FakeCommands("snap", "clpctl"):
            _, out, _ = self.cli()
        self.assertIn("No --cfToken given", out)
        self.assertFalse(init.TOKEN_FILE.exists())

    def test_installer_sha_needs_a_value(self):
        code, _, err = self.cli("--cfToken=abc", "--installerSha256")
        self.assertEqual(code, 1)
        self.assertIn("--installerSha256 needs a value", err)

    def test_certbot_failure_stops(self):
        with FakeCommands("snap", exit_code=1, error="snap: cannot communicate with server"):
            code, _, err = self.cli("--cfToken=abc", f"--installerSha256={FAKE_SHA256}")
        self.assertEqual(code, 1)
        self.assertIn("Could not install Certbot", err)
        self.assertIn("cannot communicate with server", err)
        self.assertFalse(init.TOKEN_FILE.exists())


if __name__ == "__main__":
    unittest.main()
