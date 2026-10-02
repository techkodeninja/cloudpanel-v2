import os
import tempfile
import unittest

from cloudpanel import run
from cloudpanel.errors import CommandError
from tests.helpers import FakeCommands


class RunTest(unittest.TestCase):
    def test_special_characters_pass_through_unchanged(self):
        tricky = ['p@$$ w0rd!&"x"=y', "$(rm -rf /)", "a;b|c", "`id`", "it's", "*"]
        with FakeCommands("clpctl") as fakes:
            run.clpctl(["site:add:php", *tricky])
        self.assertEqual(fakes.argvs, [["clpctl", "site:add:php", *tricky]])

    def test_returns_output(self):
        with FakeCommands("wp", output="dbuser\n"):
            self.assertEqual(run.run(["wp", "config", "get", "DB_USER"]).strip(), "dbuser")

    def test_failure_includes_command_output(self):
        with FakeCommands("clpctl", exit_code=1, error="Site already exists."):
            with self.assertRaises(CommandError) as caught:
                run.clpctl(["site:add:php", "--domainName=example.com"])
        self.assertIn("clpctl site:add:php --domainName=example.com failed", str(caught.exception))
        self.assertIn("Site already exists.", str(caught.exception))

    def test_missing_command(self):
        with self.assertRaises(CommandError) as caught:
            run.run(["definitely-not-a-real-command-xyz"])
        self.assertIn("not installed", str(caught.exception))

    def test_refuses_a_string(self):
        with self.assertRaises(TypeError):
            run.run("clpctl site:list")

    def test_a_command_asking_for_input_does_not_freeze(self):
        """A captured command gets end-of-input, never the keyboard."""
        import subprocess, sys
        code = "import sys; from cloudpanel import run; print(repr(run.run(['cat'])))"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10,
                                stdin=subprocess.PIPE)  # an open stdin that never closes
        self.assertEqual(result.stdout.strip(), "''")

    def test_input_is_passed_on_stdin(self):
        with FakeCommands("clpctl") as fakes:
            run.clpctl(["site:delete", "--domainName=example.com"], input="yes\n")
        self.assertEqual(fakes.calls[0]["stdin"], "yes\n")


class AsSiteUserTest(unittest.TestCase):
    def test_runs_through_sudo_with_home(self):
        with FakeCommands("sudo") as fakes:
            run.as_site_user("example-blog", ["wp", "core", "download", "--path=/x"])
        self.assertEqual(fakes.argvs, [["sudo", "-u", "example-blog", "-H", "wp", "core", "download", "--path=/x"]])

    @unittest.skipUnless(os.geteuid() == 0, "needs root to create a folder in /home")
    def test_runs_from_home_by_default(self):
        """From /root a site user can't read the cwd; git then refuses to start."""
        home = "/home/cp-test-site-user"
        os.makedirs(home, exist_ok=True)
        try:
            with FakeCommands("sudo") as fakes:
                run.as_site_user("cp-test-site-user", ["true"])
            self.assertEqual(fakes.calls[0]["cwd"], home)
        finally:
            os.rmdir(home)

    def test_explicit_cwd_wins(self):
        with tempfile.TemporaryDirectory() as tmp, FakeCommands("sudo") as fakes:
            run.as_site_user("nobody-here", ["true"], cwd=tmp)
        self.assertEqual(fakes.calls[0]["cwd"], os.path.realpath(tmp))


if __name__ == "__main__":
    unittest.main()
