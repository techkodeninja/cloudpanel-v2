"""A site user controls ~/.ssh: root must never write through a link there.

Each test plants what someone who broke into a site could plant, runs the
command as root would, and checks the file outside the site is untouched.
"""

import os
import stat
import unittest

from cloudpanel import ssh
from tests.helpers import FakeCommands
from tests.test_sites import FAKES, SiteTestCase

SECRET = "root:$6$secret-hash:19000:0:99999:7:::\n"


class SshSymlinkTest(SiteTestCase):
    def setUp(self):
        super().setUp()
        # Stands in for /etc/shadow: root-only, outside every site.
        self.target = self.tmp / "shadow"
        self.target.write_text(SECRET)
        os.chmod(self.target, 0o600)
        self.ssh_dir = self.home / "example" / ".ssh"
        self.ssh_dir.mkdir(parents=True)

    def assert_target_untouched(self):
        self.assertEqual(self.target.read_text(), SECRET)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)

    def plant_link(self, name):
        (self.ssh_dir / name).symlink_to(self.target)

    def test_each_file_link_is_refused(self):
        for name in ("id_ed25519", "id_ed25519.pub", "known_hosts", "authorized_keys"):
            with self.subTest(name=name):
                for child in self.ssh_dir.iterdir():
                    child.unlink()
                self.plant_link(name)
                with FakeCommands(*FAKES):
                    ssh.ensure_shared_github_key()
                    with self.assertRaises(ssh.UnsafeSshFolder):
                        if name == "authorized_keys":
                            ssh.add_authorized_keys("example", ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAInew new"])
                        else:
                            ssh.install_shared_github_key("example")
                self.assert_target_untouched()
                self.assertTrue((self.ssh_dir / name).is_symlink())  # left for you to look at

    def test_ssh_folder_itself_a_link_is_refused(self):
        self.ssh_dir.rmdir()
        elsewhere = self.tmp / "root-ssh-copy"
        elsewhere.mkdir()
        self.ssh_dir.symlink_to(elsewhere)
        with FakeCommands(*FAKES):
            ssh.ensure_shared_github_key()
            with self.assertRaises(ssh.UnsafeSshFolder):
                ssh.install_shared_github_key("example")
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_hard_link_is_refused(self):
        os.link(self.target, self.ssh_dir / "known_hosts")
        with FakeCommands(*FAKES):
            ssh.ensure_shared_github_key()
            with self.assertRaises(ssh.UnsafeSshFolder):
                ssh.install_shared_github_key("example")
        self.assert_target_untouched()

    def test_fifo_does_not_hang(self):
        os.mkfifo(self.ssh_dir / "known_hosts")
        with FakeCommands(*FAKES):
            ssh.ensure_shared_github_key()
            with self.assertRaises(ssh.UnsafeSshFolder):
                ssh.install_shared_github_key("example")

    def test_fix_ssh_reports_and_skips_the_site(self):
        self.plant_link("id_ed25519.pub")
        with FakeCommands(*FAKES):
            code, out, _ = self.cli("site:fix:ssh")
        self.assertIn("example.com", out)
        self.assertIn("possible break-in", out)

        with FakeCommands(*FAKES):
            code, out, err = self.cli("site:fix:ssh", "--domainName=example.com", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("Skipped example.com", err)
        self.assert_target_untouched()

    def test_rotate_leaves_unsafe_site_alone(self):
        with FakeCommands(*FAKES):
            ssh.ensure_shared_github_key()
        self.plant_link("id_ed25519.pub")
        with FakeCommands(*FAKES):
            code, out, err = self.cli("github:key:rotate")
        self.assertEqual(code, 1)
        self.assertIn("refusing to touch it", err)
        self.assertIn("New shared GitHub key created", out)
        self.assert_target_untouched()

    def test_normal_folder_still_works(self):
        with FakeCommands(*FAKES):
            ssh.ensure_shared_github_key()
            ssh.install_shared_github_key("example")
            added = ssh.add_authorized_keys("example", ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAInew new"])
        self.assertEqual(added, 1)
        self.assertTrue(ssh.uses_shared_github_key("example"))
        self.assertEqual(stat.S_IMODE((self.ssh_dir / "id_ed25519").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.ssh_dir / "id_ed25519.pub").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.ssh_dir.stat().st_mode), 0o700)
        self.assertIn("github.com ssh-ed25519", (self.ssh_dir / "known_hosts").read_text())


if __name__ == "__main__":
    unittest.main()
