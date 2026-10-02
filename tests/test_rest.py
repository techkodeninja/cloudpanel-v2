"""Certificates, restores, migrations and self:update."""

import os
import stat
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cloudpanel import backup
from cloudpanel.commands import self_update
from tests.helpers import FakeCommands
from tests.test_sites import FAKES, SiteTestCase


def not_after(days):
    date = datetime.now(timezone.utc) + timedelta(days=days)
    return f"notAfter={date.strftime('%b %d %H:%M:%S %Y')} GMT\n"


class CertificateTest(SiteTestCase):
    def setUp(self):
        super().setUp()
        self.live = self.tmp / "letsencrypt" / "example.com"
        self.live.mkdir(parents=True)
        for name in ("privkey.pem", "fullchain.pem", "chain.pem"):
            (self.live / name).write_text("x")
        (self.tmp / "nginx").mkdir()

    def site_cert(self, domain):
        cert = self.tmp / f"{domain}.crt"
        cert.write_text("x")
        (self.tmp / "nginx" / f"{domain}.conf").write_text(f"server {{\n  ssl_certificate {cert};\n}}\n")

    def test_install(self):
        with FakeCommands(*FAKES) as fakes:
            code, out, err = self.cli("site:install:certificate", "--domainName=api.example.com")
        self.assertEqual(code, 0, err)
        self.assertFalse(any(a[0] == "certbot" for a in fakes.argvs))  # certificate already there
        self.assertEqual(fakes.argvs[0][:3], ["clpctl", "site:install:certificate", "--domainName=api.example.com"])

    def test_update_installs_when_site_certificate_missing(self):
        with FakeCommands("clpctl", "openssl", output=not_after(80)) as fakes:
            code, out, err = self.cli("site:update:certificate", "--domainName=https://API.example.com/x")
        self.assertEqual(code, 0, err)
        install = next(a for a in fakes.argvs if a[0] == "clpctl")
        self.assertEqual(install, ["clpctl", "site:install:certificate", "--domainName=api.example.com",
                                   f"--privateKey={self.live}/privkey.pem", f"--certificate={self.live}/fullchain.pem",
                                   f"--certificateChain={self.live}/chain.pem"])
        self.assertIn("has been updated", out)

    def test_update_not_needed(self):
        self.site_cert("example.com")
        with FakeCommands("clpctl", "openssl", output=not_after(80)) as fakes:
            code, out, _ = self.cli("site:update:certificate", "--domainName=example.com")
        self.assertEqual(code, 0)
        self.assertIn("No update needed", out)
        self.assertFalse(any(a[0] == "clpctl" for a in fakes.argvs))

    def test_update_when_expiring(self):
        self.site_cert("example.com")
        with FakeCommands("clpctl", "openssl", output=not_after(10)) as fakes:
            # 10 days left on both: Let's Encrypt's own copy needs renewing first
            code, _, err = self.cli("site:update:certificate", "--domainName=example.com")
        self.assertEqual(code, 1)
        self.assertIn("needs renewing first", err)
        self.assertIn("certbot renew --cert-name example.com", err)
        self.assertFalse(any(a[0] == "clpctl" for a in fakes.argvs))


class RestoreTest(SiteTestCase):
    def setUp(self):
        super().setUp()
        (self.tmp / "nginx").mkdir()
        # What `tar -xf` leaves behind (the fake sudo doesn't run anything).
        saved = self.home / "example" / "tmp" / "backup" / "home" / "example"
        dumps = saved / "backups" / "databases" / "example"
        (dumps / "2026-01").mkdir(parents=True)
        (dumps / "old.sql.gz").write_text("old")
        (dumps / "2026-01" / "new.sql.gz").write_text("new")
        os.utime(dumps / "old.sql.gz", (1, 1))
        (saved / "htdocs" / "example.com").mkdir(parents=True)

    def test_restore(self):
        with FakeCommands(*FAKES) as fakes, \
                mock.patch.dict(os.environ, {"CP_FAKE_WP_DB_PASSWORD": "p@ss=1"}):
            code, out, err = self.cli("site:restore:wordpress", "--domainName=example.com",
                                      "--backupFile=https://dl.example.com/backup.tar?a=b")
        self.assertEqual(code, 0, err)
        argvs = fakes.argvs
        self.assertEqual(argvs[0][:4], ["clpctl", "site:add:php", "--domainName=example.com", "--phpVersion=8.3"])
        tmp = self.home / "example" / "tmp"
        user = ["sudo", "-u", "example", "-H"]
        self.assertIn(user + ["wget", "-q", "-O", f"{tmp}/backup.tar", "https://dl.example.com/backup.tar?a=b"], argvs)
        self.assertIn(user + ["mv", f"{tmp}/backup/home/example/htdocs", f"{self.home}/example/htdocs"], argvs)
        self.assertIn(["clpctl", "db:add", "--domainName=example.com", "--databaseName=example",
                       "--databaseUserName=example", "--databaseUserPassword=p@ss=1"], argvs)
        db_import = next(a for a in argvs if a[:2] == ["clpctl", "db:import"])
        self.assertTrue(db_import[3].endswith("2026-01/new.sql.gz"))  # newest dump
        cleanup = [a for a in argvs if a[4:6] == ["rm", "-rf"] and f"{tmp}/backup.tar" in a]
        self.assertEqual(len(cleanup), 2)  # before and after
        self.assertTrue(any(a[0] == "certbot" for a in argvs))
        self.assertIn("example.com has been restored", out)

    def test_existing_site_refused(self):
        (self.tmp / "nginx" / "example.com.conf").write_text("")
        with FakeCommands(*FAKES) as fakes:
            code, _, err = self.cli("site:restore:classicpress", "--domainName=example.com", "--backupFile=x")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        self.assertEqual(fakes.argvs, [])

    def test_no_password_stops_and_cleans_up(self):
        with FakeCommands(*FAKES) as fakes:
            code, _, err = self.cli("site:restore:wordpress", "--domainName=example.com", "--backupFile=x")
        self.assertEqual(code, 1)
        self.assertIn("Could not read DB_PASSWORD", err)
        self.assertFalse(any(a[:2] == ["clpctl", "db:add"] for a in fakes.argvs))
        self.assertEqual(fakes.argvs[-1][4:6], ["rm", "-rf"])


class MigrationTest(SiteTestCase):
    def setUp(self):
        super().setUp()
        self.prod = self.home / "example" / "htdocs" / "example.com"
        self.stage = self.home / "example-staging" / "htdocs" / "staging.example.com"
        (self.prod / "wp-content").mkdir(parents=True)
        (self.stage / "wp-content").mkdir(parents=True)
        self.backups = self.tmp / "backups"
        patch = mock.patch.object(backup, "BACKUP_ROOT", self.backups)
        patch.start()
        self.addCleanup(patch.stop)
        env = mock.patch.dict(os.environ, {"CP_FAKE_WP_DB_USER_example": "example",
                                           "CP_FAKE_WP_DB_USER_example-staging": "example-staging"})
        env.start()
        self.addCleanup(env.stop)

    def test_wordpress(self):
        with FakeCommands("clpctl", "sudo", "rsync", "tar") as fakes:
            code, out, err = self.cli("site:migration:wordpress", "--domainName=example.com",
                                      "--stagingName=staging.example.com", "--yes")
        self.assertEqual(code, 0, err)
        argvs = fakes.argvs
        backup_dir = next(self.backups.joinpath("example.com").iterdir())
        self.assertEqual(stat.S_IMODE(self.backups.stat().st_mode), 0o700)
        self.assertIn(["clpctl", "db:export", "--databaseName=example", f"--file={backup_dir}/example.sql.gz"], argvs)
        self.assertIn(["tar", "-czf", f"{backup_dir}/wp-content.tar.gz", "-C", str(self.prod), "wp-content"], argvs)
        export = next(a for a in argvs if a[:3] == ["clpctl", "db:export", "--databaseName=example-staging"])
        dump = export[3].split("=", 1)[1]
        self.assertIn(["rsync", "-az", "--delete", "--chown=example:example", f"{self.stage}/wp-content/", f"{self.prod}/wp-content"], argvs)
        order = [a[:2] if a[0] == "clpctl" else a[4:7] for a in argvs if a[0] == "clpctl" or a[4:5] == ["wp"]]
        self.assertLess(order.index(["wp", "db", "reset"]), order.index(["clpctl", "db:import"]))
        self.assertIn(["clpctl", "db:import", "--databaseName=example", f"--file={dump}"], argvs)
        self.assertIn(["sudo", "-u", "example", "-H", "wp", "search-replace", "//staging.example.com", "//example.com", f"--path={self.prod}"], argvs)
        self.assertFalse(Path(dump).parent.exists())  # temp dump removed

    def test_needs_confirmation(self):
        with FakeCommands("clpctl", "sudo", "rsync", "tar") as fakes:
            code, out, _ = self.cli("site:migration:classicpress", "--domainName=example.com", "--stagingName=staging.example.com")
        self.assertEqual(code, 1)
        self.assertIn("Re-run with --yes", out)
        self.assertEqual(fakes.argvs, [])
        self.assertFalse(self.backups.exists())

    def test_typed_confirmation(self):
        with FakeCommands("clpctl", "sudo", "rsync", "tar") as fakes, mock.patch("sys.stdin.isatty", return_value=True), \
                mock.patch("builtins.input", return_value="example.com"):
            code, _, err = self.cli("site:migration:wordpress", "--domainName=example.com", "--stagingName=staging.example.com")
        self.assertEqual(code, 0, err)
        self.assertTrue(any(a[0] == "rsync" for a in fakes.argvs))

    def test_failure_after_backup_says_where_it_is(self):
        from cloudpanel.commands import migrate
        from cloudpanel.errors import CommandError

        def rsync_fails(cmd, **kw):
            raise CommandError("rsync failed: disk full")

        with FakeCommands("clpctl", "sudo", "tar"), mock.patch.object(migrate, "run", rsync_fails):
            code, _, err = self.cli("site:migration:wordpress", "--domainName=example.com",
                                    "--stagingName=staging.example.com", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("Migration failed", err)
        self.assertIn("disk full", err)
        self.assertIn("Its backup from before the migration is in", err)

    def test_missing_site(self):
        code, _, err = self.cli("site:migration:wordpress", "--domainName=example.com", "--stagingName=nope.example.com", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("nope.example.com not found", err)

    def test_same_site(self):
        code, _, err = self.cli("site:migration:novaris", "--domainName=example.com", "--stagingName=example.com", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("must be different", err)

    def test_novaris(self):
        built = self.stage / "example.com"
        (built / "config").mkdir(parents=True)
        (built / ".env").write_text('APP_URI="https://staging.example.com"\nOTHER=1\n')
        (built / "config" / "app.php").write_text("<?php // APP_URI=\"https://staging.example.com\"\n")
        with FakeCommands("sudo", "rsync", "tar") as fakes:
            code, out, err = self.cli("site:migration:novaris", "--domainName=example.com",
                                      "--stagingName=staging.example.com", "--yes")
        self.assertEqual(code, 0, err)
        build = next(c for c in fakes.calls if c["argv"][0] == "sudo")
        self.assertEqual(build["argv"], ["sudo", "-u", "example-staging", "-H", "bash", "-lc", 'source "$HOME/.nvm/nvm.sh" && npm run build'])
        self.assertEqual(build["cwd"], str(self.stage))
        self.assertEqual((built / ".env").read_text(), 'APP_URI="https://example.com"\nOTHER=1\n')
        self.assertIn('"https://example.com"', (built / "config" / "app.php").read_text())
        self.assertIn(["rsync", "-az", "--delete", "--chown=example:example", f"{built}/", str(self.prod)], fakes.argvs)
        self.assertTrue(any(a[:2] == ["tar", "-czf"] and a[-1] == "example.com" for a in fakes.argvs))


class SelfUpdateTest(SiteTestCase):
    def bundle(self, path, version="9.9.9"):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("__main__.py", f"print('cloudpanel {version}')\n")
        data = path.read_bytes()
        path.write_bytes(b"#!/usr/bin/env python3\n" + data)
        return path.read_bytes()

    def fake_urlopen(self, data):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = data
        return mock.patch.object(self_update.urllib.request, "urlopen", return_value=response)

    def test_replaces_itself(self):
        target = self.tmp / "cloudpanel"
        self.bundle(target, "1.0.0")
        new = self.bundle(self.tmp / "new", "9.9.9")
        with self.fake_urlopen(new) as urlopen, mock.patch.object(sys, "argv", [str(target), "self:update"]):
            code, out, err = self.cli("self:update", "--branch=step-7-rest")
        self.assertEqual(code, 0, err)
        self.assertIn("/step-7-rest/dist/cloudpanel", urlopen.call_args[0][0])
        self.assertEqual(target.read_bytes(), new)
        self.assertIn("9.9.9", out)
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(".cloudpanel-")], [])

    def test_refuses_a_bad_download(self):
        target = self.tmp / "cloudpanel"
        old = self.bundle(target, "1.0.0")
        with self.fake_urlopen(b"<html>404</html>"), mock.patch.object(sys, "argv", [str(target)]):
            code, _, err = self.cli("self:update")
        self.assertEqual(code, 1)
        self.assertIn("is not a cloudpanel bundle", err)
        self.assertEqual(target.read_bytes(), old)

    def test_from_source(self):
        with mock.patch.object(sys, "argv", ["/nope/cloudpanel/__main__.py"]):
            code, _, err = self.cli("self:update")
        self.assertEqual(code, 1)
        self.assertIn("Running from source", err)


if __name__ == "__main__":
    import unittest
    unittest.main()
