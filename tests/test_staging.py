"""site:staging:create, and pushing staging back with site:migration:wordpress."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cloudpanel import backup
from cloudpanel.commands import staging
from tests.helpers import FakeCommands
from tests.test_sites import FAKES, SiteTestCase

STAGING_FAKES = (*FAKES, "rsync", "tar")


class StagingTestCase(SiteTestCase):
    def setUp(self):
        super().setUp()
        (self.tmp / "nginx").mkdir()
        (self.tmp / "nginx" / "example.com.conf").write_text("server { server_name example.com; }\n")
        self.live = self.home / "example" / "htdocs" / "example.com"
        self.stage = self.home / "example-staging" / "htdocs" / "staging.example.com"
        (self.live / "wp-content" / "uploads").mkdir(parents=True)
        (self.live / "wp-config.php").write_text("<?php define('DB_NAME', 'example');\n")
        # rsync is a stand-in, so put the "copied" wp-config.php in place.
        self.stage.mkdir(parents=True)
        (self.stage / "wp-config.php").write_text("<?php define('DB_NAME', 'example');\n")
        self.backups = self.tmp / "backups"
        for patch in (mock.patch.object(backup, "BACKUP_ROOT", self.backups),
                      mock.patch.dict(os.environ, {"CP_FAKE_WP_DB_NAME_example": "example"})):
            patch.start()
            self.addCleanup(patch.stop)

    def wp_calls(self, fakes, user):
        prefix = ["sudo", "-u", user, "-H", "wp"]
        return [a[5:] for a in fakes.argvs if a[:5] == prefix]

    def existing_staging(self):
        (self.tmp / "nginx" / "staging.example.com.conf").write_text("server {}\n")
        os.environ.update({"CP_FAKE_WP_DB_NAME_example-staging": "stagedb",
                           "CP_FAKE_WP_DB_USER_example-staging": "stageuser",
                           "CP_FAKE_WP_DB_PASSWORD_example-staging": "stagepass"})
        for key in ("NAME", "USER", "PASSWORD"):
            self.addCleanup(os.environ.pop, f"CP_FAKE_WP_DB_{key}_example-staging", None)


class CreateStagingTest(StagingTestCase):
    def test_first_time_creates_the_site_and_copies_everything(self):
        with FakeCommands(*STAGING_FAKES) as fakes:
            code, out, err = self.cli("site:staging:create", "--domainName=example.com")
        self.assertEqual(code, 0, err)
        argvs = fakes.argvs
        add = next(a for a in argvs if a[0] == "clpctl")
        self.assertEqual(add[:3], ["clpctl", "site:add:php", "--domainName=staging.example.com"])
        self.assertIn("--siteUser=example-staging", add)
        db_add = next(a for a in argvs if a[:2] == ["clpctl", "db:add"])
        self.assertIn("--databaseName=example-staging", db_add)
        password = next(a for a in db_add if a.startswith("--databaseUserPassword=")).split("=", 1)[1]

        export = next(a for a in argvs if a[:2] == ["clpctl", "db:export"])
        self.assertEqual(export[2], "--databaseName=example")  # the live database, read only
        dump = export[3].split("=", 1)[1]
        self.assertIn(["rsync", "-az", "--delete", "--chown=example-staging:example-staging",
                       "--exclude=/wp-content/mu-plugins/cloudpanel-staging.php", f"{self.live}/", str(self.stage)], argvs)

        wp = self.wp_calls(fakes, "example-staging")
        path = f"--path={self.stage}"
        for key, value in (("DB_NAME", "example-staging"), ("DB_USER", "example-staging"),
                           ("DB_PASSWORD", password), ("DB_HOST", "127.0.0.1"), ("WP_ENVIRONMENT_TYPE", "staging")):
            self.assertIn(["config", "set", key, value, path, "--quiet"], wp)
        self.assertNotIn("WP_HOME", str(wp))  # live doesn't set it, so neither does staging
        order = [a[:2] if a[0] == "clpctl" else a[4:7] for a in argvs if a[0] == "clpctl" or a[4:5] == ["wp"]]
        self.assertLess(order.index(["wp", "db", "reset"]), order.index(["clpctl", "db:import"]))
        self.assertIn(["clpctl", "db:import", "--databaseName=example-staging", f"--file={dump}"], argvs)
        self.assertIn(["search-replace", "//example.com", "//staging.example.com", path, "--quiet"], wp)
        self.assertIn(["option", "update", "blog_public", "0", path, "--quiet"], wp)

        guard = next(c for c in fakes.calls if c["argv"][4:5] == ["tee"])
        self.assertEqual(guard["argv"][:5], ["sudo", "-u", "example-staging", "-H", "tee"])
        self.assertEqual(guard["argv"][5], str(self.stage / "wp-content" / "mu-plugins" / "cloudpanel-staging.php"))
        self.assertEqual(guard["stdin"], staging.guard_plugin("staging.example.com"))

        # The live site is only read: nothing is reset, imported or replaced there.
        self.assertEqual([c for c in self.wp_calls(fakes, "example") if c[0] != "config"], [])
        self.assertNotIn("--databaseName=example", [a[2] for a in argvs if a[:2] == ["clpctl", "db:import"]])
        self.assertFalse(Path(dump).parent.exists())  # temp dump removed
        self.assertIn("Staging is ready: https://staging.example.com", out)
        self.assertIn("--filesOnly", out)

    def test_new_staging_is_removed_when_the_copy_fails(self):
        with FakeCommands(*STAGING_FAKES) as fakes, mock.patch.object(
                staging, "run", side_effect=staging.CloudPanelError("rsync failed: disk full")):
            code, out, err = self.cli("site:staging:create", "--domainName=example.com")
        self.assertEqual(code, 1)
        self.assertIn("disk full", err)
        self.assertNotIn("may be incomplete", err)
        self.assertIn(["clpctl", "site:delete", "--domainName=staging.example.com"], fakes.argvs)
        self.assertFalse(any(a[:2] == ["clpctl", "site:delete"] and "--domainName=example.com" in a
                             for a in fakes.argvs))

    def test_existing_staging_is_kept_when_the_copy_fails(self):
        self.existing_staging()
        with FakeCommands(*STAGING_FAKES) as fakes, mock.patch.object(
                staging, "run", side_effect=staging.CloudPanelError("rsync failed: disk full")):
            code, _, err = self.cli("site:staging:create", "--domainName=example.com", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("may be incomplete", err)
        self.assertFalse(any(a[:2] == ["clpctl", "site:delete"] for a in fakes.argvs))

    def test_custom_name_and_live_address_settings(self):
        (self.live / "wp-config.php").write_text("<?php define( 'WP_HOME', 'https://example.com' );\n")
        stage = self.home / "example-test" / "htdocs" / "test.example.com"
        stage.mkdir(parents=True)
        (stage / "wp-config.php").write_text("<?php define( 'WP_HOME', 'https://example.com' );\n")
        with FakeCommands(*STAGING_FAKES) as fakes:
            code, _, err = self.cli("site:staging:create", "--domainName=example.com", "--stagingName=Test.Example.com")
        self.assertEqual(code, 0, err)
        wp = self.wp_calls(fakes, "example-test")
        self.assertIn(["config", "set", "WP_HOME", "https://test.example.com", f"--path={stage}", "--quiet"], wp)
        self.assertNotIn(["config", "set", "WP_SITEURL", "https://test.example.com", f"--path={stage}", "--quiet"], wp)

    def test_existing_staging_needs_confirmation(self):
        self.existing_staging()
        with FakeCommands(*STAGING_FAKES) as fakes:
            code, out, _ = self.cli("site:staging:create", "--domainName=example.com")
        self.assertEqual(code, 1)
        self.assertIn("This replaces everything on", out)
        self.assertIn("Re-run with --yes", out)
        self.assertFalse(any(a[0] in ("clpctl", "rsync") for a in fakes.argvs))

    def test_existing_staging_is_refreshed_with_its_own_database(self):
        self.existing_staging()
        with FakeCommands(*STAGING_FAKES) as fakes:
            code, _, err = self.cli("site:staging:create", "--domainName=example.com", "--yes")
        self.assertEqual(code, 0, err)
        self.assertFalse(any(a[:2] in (["clpctl", "site:add:php"], ["clpctl", "db:add"]) for a in fakes.argvs))
        wp = self.wp_calls(fakes, "example-staging")
        for key, value in (("DB_NAME", "stagedb"), ("DB_USER", "stageuser"), ("DB_PASSWORD", "stagepass")):
            self.assertIn(["config", "set", key, value, f"--path={self.stage}", "--quiet"], wp)
        self.assertTrue(any(a[:3] == ["clpctl", "db:import", "--databaseName=stagedb"] for a in fakes.argvs))

    def test_refuses_what_it_cant_copy(self):
        (self.live / "wp-config.php").unlink()
        code, _, err = self.cli("site:staging:create", "--domainName=example.com")
        self.assertEqual(code, 1)
        self.assertIn("isn't a WordPress or ClassicPress site", err)

        (self.live / "wp-config.php").write_text("<?php\n")
        with FakeCommands(*STAGING_FAKES) as fakes, mock.patch.dict(os.environ, {"CP_FAKE_WP_MULTISITE_example": "true"}):
            code, _, err = self.cli("site:staging:create", "--domainName=example.com", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("multisite", err)
        self.assertFalse(any(a[0] == "clpctl" for a in fakes.argvs))

        (self.tmp / "nginx" / "staging.example.com.conf").write_text("server {}\n")
        (self.stage / "wp-config.php").unlink()
        with FakeCommands(*STAGING_FAKES):
            code, _, err = self.cli("site:staging:create", "--domainName=example.com", "--yes")
        self.assertIn("isn't a WordPress site", err)

    def test_bad_names(self):
        for args, message in ((["--stagingName=example.com"], "must be a different site"),
                              (["--stagingName=../etc"], "must be a plain domain name"),
                              (["--stagingName"], "needs a value")):
            with self.subTest(args=args):
                code, _, err = self.cli("site:staging:create", "--domainName=example.com", *args)
                self.assertEqual(code, 1)
                self.assertIn(message, err)


@unittest.skipUnless(shutil.which("php"), "php not installed")
class GuardPluginTest(unittest.TestCase):
    """Run the guard with tiny stand-ins for the WordPress functions it uses."""

    STUBS = r"""<?php
define('ABSPATH', '/');
$hooks = [];
function add_filter($name, $fn) { global $hooks; $hooks[$name] = $fn; }
function add_action($name, $fn) { global $hooks; $hooks[$name] = $fn; }
function home_url() { return getenv('HOME_URL'); }
function __return_false() { return false; }
function is_user_logged_in() { return getenv('LOGGED_IN') === '1'; }
function auth_redirect() { echo "redirect-to-login\n"; }
class WP_Error { function __construct($code) { $this->code = $code; } }
require getenv('GUARD');
echo implode(',', array_keys($hooks)), "\n";
if (isset($hooks['template_redirect'])) { $hooks['template_redirect'](); }
if (isset($hooks['pre_option_blog_public'])) { var_dump($hooks['pre_option_blog_public']()); }
if (isset($hooks['rest_authentication_errors'])) { $r = $hooks['rest_authentication_errors'](null); echo is_object($r) ? $r->code : 'allowed', "\n"; }
"""

    def run_guard(self, home_url, logged_in=False):
        with tempfile.TemporaryDirectory() as folder:
            guard, stubs = Path(folder) / "guard.php", Path(folder) / "stubs.php"
            guard.write_text(staging.guard_plugin("staging.example.com"))
            stubs.write_text(self.STUBS)
            lint = subprocess.run(["php", "-l", str(guard)], capture_output=True, text=True)
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)
            env = {**os.environ, "GUARD": str(guard), "HOME_URL": home_url, "LOGGED_IN": "1" if logged_in else "0"}
            result = subprocess.run(["php", str(stubs)], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

    def test_private_on_staging(self):
        out = self.run_guard("https://staging.example.com")
        self.assertIn("pre_wp_mail,pre_option_blog_public,send_headers,template_redirect,rest_authentication_errors", out)
        self.assertIn("redirect-to-login", out)
        self.assertIn('string(1) "0"', out)
        self.assertIn("staging_private", out)

    def test_logged_in_can_see_it(self):
        out = self.run_guard("https://Staging.Example.com/", logged_in=True)
        self.assertNotIn("redirect-to-login", out)
        self.assertIn("allowed", out)

    def test_does_nothing_on_the_live_site(self):
        out = self.run_guard("https://example.com")
        self.assertEqual(out.strip(), "")


class PushToLiveTest(StagingTestCase):
    def setUp(self):
        super().setUp()
        for folder in ("themes", "plugins", "mu-plugins", "uploads"):
            (self.stage / "wp-content" / folder).mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"CP_FAKE_WP_DB_USER_example": "example",
                                           "CP_FAKE_WP_DB_USER_example-staging": "example-staging",
                                           "CP_FAKE_WP_OPTION_blog_public_example": "1"})
        env.start()
        self.addCleanup(env.stop)

    def test_files_only_keeps_the_live_database_and_uploads(self):
        with FakeCommands(*STAGING_FAKES) as fakes:
            code, out, err = self.cli("site:migration:wordpress", "--domainName=example.com",
                                      "--stagingName=staging.example.com", "--filesOnly", "--yes")
        self.assertEqual(code, 0, err)
        rsyncs = [a for a in fakes.argvs if a[0] == "rsync"]
        self.assertEqual([a[-1] for a in rsyncs], [f"{self.live}/wp-content/{f}" for f in ("themes", "plugins", "mu-plugins")])
        for a in rsyncs:
            self.assertIn("--exclude=/cloudpanel-staging.php", a)
        self.assertFalse(any(a[0] == "clpctl" for a in fakes.argvs))  # no database export, reset or import
        self.assertEqual([c[:2] for c in self.wp_calls(fakes, "example")], [["cache", "flush"]])
        backup_dir = next((self.backups / "example.com").iterdir())
        self.assertTrue(any(a[:2] == ["tar", "-czf"] and a[2] == f"{backup_dir}/wp-content.tar.gz" for a in fakes.argvs))
        self.assertIn("database and uploads are unchanged", out)

    def test_files_only_needs_confirmation(self):
        with FakeCommands(*STAGING_FAKES) as fakes:
            code, out, _ = self.cli("site:migration:wordpress", "--domainName=example.com",
                                    "--stagingName=staging.example.com", "--filesOnly")
        self.assertEqual(code, 1)
        self.assertIn("Its database and uploads are kept", out)
        self.assertEqual(fakes.argvs, [])

    def test_full_push_keeps_the_live_search_engine_setting(self):
        with FakeCommands(*STAGING_FAKES) as fakes, \
                mock.patch.dict(os.environ, {"CP_FAKE_WP_OPTION_blog_public_example": "0"}):
            code, _, err = self.cli("site:migration:wordpress", "--domainName=example.com",
                                    "--stagingName=staging.example.com", "--yes")
        self.assertEqual(code, 0, err)
        wp = [c[:4] for c in self.wp_calls(fakes, "example")]
        self.assertLess(wp.index(["option", "get", "blog_public", f"--path={self.live}"]), wp.index(["db", "reset", "--yes", f"--path={self.live}"]))
        self.assertIn(["option", "update", "blog_public", "0"], wp)  # live's own setting, not staging's
        self.assertLess(wp.index(["search-replace", "//staging.example.com", "//example.com", f"--path={self.live}"]),
                        wp.index(["option", "update", "blog_public", "0"]))


if __name__ == "__main__":
    unittest.main()
