from unittest import mock

from cloudpanel import nginx, run
from cloudpanel.commands import wordpress
from tests.helpers import FakeCommands
from tests.test_sites import FAKES, SiteTestCase

TRICKY = 'p@$$ w0rd!&"x"=y'


class WordPressTest(SiteTestCase):
    def setUp(self):
        super().setUp()
        nginx_dir = self.tmp / "nginx"
        nginx_dir.mkdir()

        # clpctl stand-in: record like the others, and on site:add:php write a
        # vhost the way CloudPanel does (with www1.<domain>).
        real_clpctl = run.clpctl

        def clpctl(args, **options):
            result = real_clpctl(args, **options)
            if args[0] == "site:add:php":
                domain = args[1].split("=", 1)[1]
                server = f"server_name {domain} www1.{domain};"
                (nginx_dir / f"{domain}.conf").write_text(
                    f"server {{\n  listen 80;\n  {server}\n}}\nserver {{\n  listen 443 ssl;\n  {server}\n}}\n"
                )
            return result

        patch = mock.patch.object(wordpress, "clpctl", clpctl)
        patch.start()
        self.addCleanup(patch.stop)

    def wp_calls(self, argvs, user):
        return [a[5:] for a in argvs if a[:5] == ["sudo", "-u", user, "-H", "wp"]]

    def test_wordpress_single(self):
        with FakeCommands(*FAKES) as fakes:
            code, out, err = self.cli("site:add:wordpress", "--domainName=example.com", "--wpAdmin=ben",
                                      f"--wpPassword={TRICKY}", "--wpEmail=ben@example.com")
        self.assertEqual(code, 0, err)
        argvs = fakes.argvs
        self.assertEqual(argvs[0][:5], ["clpctl", "site:add:php", "--domainName=example.com", "--phpVersion=8.3", "--vhostTemplate=WordPress"])
        db_add = next(a for a in argvs if a[:2] == ["clpctl", "db:add"])
        self.assertIn("--databaseName=example", db_add)
        db_password = next(a for a in db_add if a.startswith("--databaseUserPassword=")).split("=", 1)[1]

        path = f"--path={self.home}/example/htdocs/example.com"
        wp = self.wp_calls(argvs, "example")
        self.assertEqual(wp[0], ["core", "download", path, "--quiet"])
        self.assertIn(f"--dbpass={db_password}", wp[1])  # same password as the database
        self.assertEqual(wp[2][:2], ["core", "install"])
        self.assertIn(f"--admin_password={TRICKY}", wp[2])  # special characters intact
        self.assertEqual(wp[3][:5], ["config", "set", "DISALLOW_FILE_EDIT", "true", "--raw"])

        vhost = (self.tmp / "nginx" / "example.com.conf").read_text()
        self.assertNotIn("www1.example.com", vhost)
        self.assertNotIn("*.example.com", vhost)
        self.assertTrue(any(a[0] == "certbot" for a in argvs))
        self.assertTrue((self.home / "example" / ".ssh" / "id_ed25519").exists())  # shared key
        self.assertIn("WordPress installed successfully", out)

    def test_classicpress_subdirectory(self):
        with FakeCommands(*FAKES) as fakes:
            code, out, err = self.cli("site:add:classicpress", "--domainName=api.example.com", "--cpAdmin=ben",
                                      "--cpPassword=a=b", "--cpEmail=b@example.com", "--cpType=subdirectory",
                                      "--phpVersion=8.4")
        self.assertEqual(code, 0, err)
        self.assertIn("--phpVersion=8.4", fakes.argvs[0])
        wp = self.wp_calls(fakes.argvs, "example-api")
        self.assertEqual(wp[0][:3], ["core", "download", "https://www.classicpress.net/latest.zip"])
        self.assertEqual(wp[2][:2], ["core", "multisite-install"])
        self.assertNotIn("--subdomains", wp[2])
        self.assertIn("--admin_password=a=b", wp[2])
        self.assertIn("ClassicPress Multisite installed successfully", out)

    def test_subdomain_multisite(self):
        with FakeCommands(*FAKES, "nginx", "systemctl") as fakes:
            code, out, err = self.cli("site:add:wordpress", "--domainName=example.com", "--wpAdmin=ben",
                                      "--wpPassword=x", "--wpEmail=b@example.com", "--wpType=subdomain")
        self.assertEqual(code, 0, err)
        vhost = (self.tmp / "nginx" / "example.com.conf").read_text()
        self.assertEqual(vhost.count("server_name example.com *.example.com;"), 2)
        self.assertIn(["nginx", "-t"], fakes.argvs)
        self.assertIn(["systemctl", "reload", "nginx"], fakes.argvs)
        wp = self.wp_calls(fakes.argvs, "example")
        self.assertEqual(wp[2][:3], ["core", "multisite-install", "--subdomains"])
        self.assertIn("One more step for subdomain sites", out)

    def test_subdomain_needs_main_domain(self):
        with FakeCommands(*FAKES) as fakes:
            code, _, err = self.cli("site:add:wordpress", "--domainName=blog.example.com", "--wpAdmin=ben",
                                    "--wpPassword=x", "--wpEmail=b@example.com", "--wpType=subdomain")
        self.assertEqual(code, 1)
        self.assertIn("needs the main domain (e.g. example.com)", err)
        self.assertEqual(fakes.argvs, [])

    def test_invalid_type(self):
        code, _, err = self.cli("site:add:classicpress", "--domainName=example.com", "--cpAdmin=ben",
                                "--cpPassword=x", "--cpEmail=b@example.com", "--cpType=subdomains")
        self.assertEqual(code, 1)
        self.assertIn("--cpType must be 'single', 'subdirectory', 'subdomain'", err)

    def test_missing_admin_password(self):
        code, _, err = self.cli("site:add:wordpress", "--domainName=example.com", "--wpAdmin=ben", "--wpEmail=b@example.com")
        self.assertEqual(code, 1)
        self.assertIn("Missing --wpPassword", err)

    def test_nginx_check_failure_restores_vhost(self):
        original_run = nginx.run

        def failing_run(cmd, **kw):
            if cmd[:2] == ["nginx", "-t"]:
                raise wordpress.CloudPanelError("nginx: [emerg] invalid server name")
            return original_run(cmd, **kw)

        with FakeCommands(*FAKES, "systemctl") as fakes, mock.patch.object(nginx, "run", failing_run):
            code, _, err = self.cli("site:add:wordpress", "--domainName=example.com", "--wpAdmin=ben",
                                    "--wpPassword=x", "--wpEmail=b@example.com", "--wpType=subdomain")
        self.assertEqual(code, 1)
        self.assertIn("Could not set up *.example.com in nginx", err)
        self.assertIn("invalid server name", err)
        vhost = (self.tmp / "nginx" / "example.com.conf").read_text()
        self.assertNotIn("*.example.com", vhost)  # rolled back
        self.assertFalse(any(a[:2] == ["clpctl", "db:add"] for a in fakes.argvs))  # stopped before WordPress

    def test_wp_failure_reports_wp_output(self):
        with FakeCommands(*FAKES) as fakes, mock.patch.object(
                wordpress, "as_site_user", side_effect=wordpress.CloudPanelError("wp core download failed: Error: Could not resolve host")):
            code, _, err = self.cli("site:add:wordpress", "--domainName=example.com", "--wpAdmin=ben",
                                    "--wpPassword=x", "--wpEmail=b@example.com")
        self.assertEqual(code, 1)
        self.assertIn("WordPress install failed for example.com", err)
        self.assertIn("Could not resolve host", err)
        self.assertFalse(any(a[0] == "certbot" for a in fakes.argvs))


if __name__ == "__main__":
    import unittest
    unittest.main()
