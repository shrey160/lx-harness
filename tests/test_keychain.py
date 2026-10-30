"""A test run can never reach the real keychain (the dummy-key slip of 30 Oct 2026)."""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from lx.keychain import TEST_RUN, SystemKeychain, test_run as in_test_run


class RefusalTests(unittest.TestCase):
    def test_this_run_is_marked_and_refuses_every_call(self):
        self.assertTrue(in_test_run())
        keychain = SystemKeychain()
        self.assertFalse(keychain.available())
        for call in (lambda: keychain.set("dummy", "dummy"), lambda: keychain.get("dummy"), lambda: keychain.delete("dummy")):
            with patch("keyring.set_password") as written, patch("keyring.get_password") as read, patch("keyring.delete_password") as removed:
                with self.assertRaises(RuntimeError):
                    call()
            for reached in (written, read, removed):
                reached.assert_not_called()

    def test_a_worker_this_run_starts_inherits_the_refusal(self):
        script = "from lx.keychain import SystemKeychain, test_run; print(test_run(), SystemKeychain().available())"
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
        self.assertEqual(done.stdout.split(), ["True", "False"])

    def test_the_app_outside_a_test_run_is_not_marked(self):
        env = {k: v for k, v in os.environ.items() if k != TEST_RUN}
        script = "from lx.keychain import test_run; print(test_run())"
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True, env=env)
        self.assertEqual(done.stdout.split(), ["False"])
