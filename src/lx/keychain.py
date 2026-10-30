"""The operating system's keychain (Windows Credential Manager, macOS Keychain, Secret Service).

Keys are stored under an opaque name. Project folders and records hold only
that name. Headless Linux and WSL usually have no keychain; available() says so
and the registry falls back to its owner-only files there.
"""
from __future__ import annotations

SERVICE = "lx-harness"


class SystemKeychain:
    def available(self) -> bool:
        try:
            import keyring
            from keyring.backends import fail, null
            backend = keyring.get_keyring()
            return not isinstance(backend, (fail.Keyring, null.Keyring)) and backend.priority > 0
        except Exception:
            return False

    def set(self, name: str, value: str) -> None:
        import keyring
        keyring.set_password(SERVICE, name, value)

    def get(self, name: str) -> str | None:
        import keyring
        return keyring.get_password(SERVICE, name)

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError
        try:
            keyring.delete_password(SERVICE, name)
        except PasswordDeleteError:
            pass
