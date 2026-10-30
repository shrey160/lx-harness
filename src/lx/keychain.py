"""The operating system's keychain (Windows Credential Manager, macOS Keychain, Secret Service).

Keys are stored under an opaque name. Project folders and records hold only
that name. Headless Linux and WSL usually have no keychain; available() says so
and the registry falls back to its owner-only files there.

A test run has no keychain: tests use dummy keys, and one once landed in the
real Credential Manager. Workers a test run starts inherit the marker.
"""
from __future__ import annotations

import os
import sys

SERVICE = "lx-harness"
TEST_RUN = "LX_TEST_RUN"

_started_by = getattr(getattr(sys.modules.get("__main__"), "__spec__", None), "name", "") or ""
if _started_by.split(".")[0] in {"unittest", "pytest"}:
    os.environ[TEST_RUN] = "1"


def test_run() -> bool:
    return os.environ.get(TEST_RUN) == "1"


def _refuse_in_tests() -> None:
    if test_run():
        raise RuntimeError("A test run must not reach the real keychain; use a fake one")


class SystemKeychain:
    def available(self) -> bool:
        if test_run():
            return False
        try:
            import keyring
            from keyring.backends import fail, null
            backend = keyring.get_keyring()
            return not isinstance(backend, (fail.Keyring, null.Keyring)) and backend.priority > 0
        except Exception:
            return False

    def set(self, name: str, value: str) -> None:
        _refuse_in_tests()
        import keyring
        keyring.set_password(SERVICE, name, value)

    def get(self, name: str) -> str | None:
        _refuse_in_tests()
        import keyring
        return keyring.get_password(SERVICE, name)

    def delete(self, name: str) -> None:
        _refuse_in_tests()
        import keyring
        from keyring.errors import PasswordDeleteError
        try:
            keyring.delete_password(SERVICE, name)
        except PasswordDeleteError:
            pass
