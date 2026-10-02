import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cloudpanel import __version__, cli, ui

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*argv):
    """Run the CLI in-process; return (exit_code, stdout, stderr)."""
    ui.exit_code = 0
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ParseArgsTest(unittest.TestCase):
    def test_splits_at_first_equals_only(self):
        params = cli.parse_args([
            "--backupFile=https://x.com/b.tar?token=abc=&sig=x y",
            "--wpPassword=a=b",
            "--domainName=example.com",
        ])
        self.assertEqual(params["backupFile"], "https://x.com/b.tar?token=abc=&sig=x y")
        self.assertEqual(params["wpPassword"], "a=b")
        self.assertEqual(params["domainName"], "example.com")

    def test_bare_flag_is_true(self):
        self.assertEqual(cli.parse_args(["--apply", "--yes"]), {"apply": True, "yes": True})

    def test_values_stay_strings(self):
        self.assertEqual(cli.parse_args(["--cpPassword=1234567890"])["cpPassword"], "1234567890")

    def test_empty_value_and_non_options(self):
        self.assertEqual(cli.parse_args(["--name=", "stray", "-x"]), {"name": ""})


class MainTest(unittest.TestCase):
    def test_help(self):
        for argv in ([], ["--help"], ["-h"], ["help"]):
            code, out, _ = run_cli(*argv)
            self.assertEqual(code, 0)
            self.assertIn("site:add:wordpress", out)
            self.assertIn("github:key:rotate", out)

    def test_version(self):
        code, out, _ = run_cli("--version")
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)

    def test_unknown_command(self):
        code, out, err = run_cli("site:add:jigsaw")
        self.assertEqual(code, 1)
        self.assertIn("Unknown command: site:add:jigsaw", err)
        self.assertIn("site:add:php", out)

    def test_command_not_ported_yet(self):
        with mock.patch.dict(cli.COMMANDS, {"site:add:jigsaw": None}):
            code, _, err = run_cli("site:add:jigsaw")
        self.assertEqual(code, 2)
        self.assertIn("isn't available in this version yet", err)

    def test_every_help_command_is_registered(self):
        _, out, _ = run_cli("--help")
        for word in out.split():
            if word.count(":") >= 1 and word[0].isalpha() and not word.startswith(("http", "--")):
                if word.split(":")[0] in ("init", "site", "github", "self"):
                    self.assertIn(word, cli.COMMANDS, f"{word} is in the help but not registered")


class BuildTest(unittest.TestCase):
    def test_bundle_runs_on_its_own(self):
        """build.sh produces one file that runs with nothing but python3."""
        subprocess.run(["bash", str(ROOT / "build.sh")], check=True, capture_output=True)
        bundle = ROOT / "dist" / "cloudpanel"
        self.assertTrue(os.access(bundle, os.X_OK))

        # Run it from an empty folder with a clean environment, so it can't
        # pick up the source tree.
        with tempfile.TemporaryDirectory() as empty:
            result = subprocess.run(
                [str(bundle), "--version"],
                cwd=empty, capture_output=True, text=True,
                env={"PATH": os.environ["PATH"]},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(__version__, result.stdout)


if __name__ == "__main__":
    unittest.main()
