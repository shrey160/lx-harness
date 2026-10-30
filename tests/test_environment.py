"""The running environment matches uv.lock, so a stale .venv fails here rather than somewhere odd."""
from importlib import metadata
from pathlib import Path
import re
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


class LockTests(unittest.TestCase):
    def setUp(self):
        with (ROOT/"uv.lock").open("rb") as lock:
            self.locked = {normalized(p["name"]): p["version"] for p in tomllib.load(lock)["package"]}

    def test_every_installed_locked_package_has_the_locked_version(self):
        installed = {normalized(d.metadata["Name"]): d.version for d in metadata.distributions()}
        stale = {name: (installed[name], version) for name, version in self.locked.items()
                 if name in installed and installed[name] != version}
        self.assertEqual(stale, {}, "Run uv sync: installed and locked versions differ")

    def test_every_direct_dependency_is_installed(self):
        with (ROOT/"pyproject.toml").open("rb") as project:
            direct = [re.split(r"[=<>!~; ]", item)[0] for item in tomllib.load(project)["project"]["dependencies"]]
        for name in direct:
            self.assertIn(normalized(name), self.locked)
            self.assertEqual(metadata.version(name), self.locked[normalized(name)], "Run uv sync")
