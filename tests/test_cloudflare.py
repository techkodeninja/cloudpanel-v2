import io
import json
import stat
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from cloudpanel import certificates, cli, ui
from cloudpanel.commands import cloudflare


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def cloudflare_says(zones=("example.com",), status=200):
    def urlopen(request, timeout=None):
        assert request.get_header("Authorization") == "Bearer new-token=1"
        if status != 200:
            body = io.BytesIO(json.dumps({"success": False, "errors": [{"message": "Invalid API Token"}]}).encode())
            raise urllib.error.HTTPError(request.full_url, status, "Bad", {}, body)
        return FakeResponse(json.dumps({"success": True, "result": [{"name": z} for z in zones]}).encode())
    return mock.patch.object(cloudflare.urllib.request, "urlopen", urlopen)


class CloudflareTokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.token_file = self.tmp / ".cloudflare" / "token"
        self.token_file.parent.mkdir()
        self.token_file.write_text("dns_cloudflare_api_token = old-token\n")
        patch = mock.patch.object(certificates, "CLOUDFLARE_CREDENTIALS", self.token_file)
        patch.start()
        self.addCleanup(patch.stop)

    def cli(self, *argv):
        ui.exit_code = 0
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["cloudflare:token", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_replaces_token_after_checking_it(self):
        with cloudflare_says(["example.com", "codestacks.cc"]):
            code, out, err = self.cli("--cfToken=new-token=1")
        self.assertEqual(code, 0, err)
        self.assertIn("dns_cloudflare_api_token = new-token=1", self.token_file.read_text())
        self.assertNotIn("old-token", self.token_file.read_text())
        self.assertEqual(stat.S_IMODE(self.token_file.stat().st_mode), 0o600)
        self.assertIn("example.com, codestacks.cc", out)
        self.assertIn("delete it in Cloudflare", out)
        self.assertNotIn("new-token", out)  # never printed

    def test_asks_when_no_option(self):
        with cloudflare_says(), mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch.object(cloudflare.getpass, "getpass", return_value=" new-token=1 "):
            code, _, err = self.cli()
        self.assertEqual(code, 0, err)
        self.assertIn("= new-token=1\n", self.token_file.read_text())

    def test_bad_token_changes_nothing(self):
        with cloudflare_says(status=401):
            code, _, err = self.cli("--cfToken=new-token=1")
        self.assertEqual(code, 1)
        self.assertIn("Cloudflare rejected the token (401: Invalid API Token)", err)
        self.assertIn("old-token", self.token_file.read_text())

    def test_token_without_zones_changes_nothing(self):
        with cloudflare_says(zones=[]):
            code, _, err = self.cli("--cfToken=new-token=1")
        self.assertEqual(code, 1)
        self.assertIn("can't see any domains", err)
        self.assertIn("old-token", self.token_file.read_text())

    def test_no_terminal_no_option(self):
        code, _, err = self.cli()
        self.assertEqual(code, 1)
        self.assertIn("Use --cfToken", err)


if __name__ == "__main__":
    unittest.main()
