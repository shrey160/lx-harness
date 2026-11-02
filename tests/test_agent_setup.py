"""Connect an agent: Settings gives ready setup, with this install's paths and no keys."""
import json
import sys
import tomllib
import unittest
from unittest.mock import patch

from test_tasks import Base


class Access(Base):
    def test_agents_are_allowed_until_a_person_stops_them(self):
        self.assertIs(self.app.system()["agent_access"], True)
        self.app.set_agent_access(False)
        self.assertIs(self.app.system()["agent_access"], False)
        self.app.set_agent_access(True)
        self.assertIs(self.app.agent_access(), True)

    def test_only_true_or_false_is_taken(self):
        for value in (None, "off", 0):
            with self.subTest(value), self.assertRaisesRegex(ValueError, "agents may connect"):
                self.app.set_agent_access(value)


class Setup(Base):
    def test_codex_config_starts_the_same_server_and_private_state_as_muse(self):
        setup = self.app.agent_setup()
        codex = tomllib.loads(setup["codex_config"])["mcp_servers"]["lx"]
        muse = json.loads(setup["muse"])["mcp_servers"]["lx"]
        self.assertEqual(codex["command"], muse["command"])
        self.assertEqual(codex["args"], muse["args"])
        self.assertIn(str(self.app.root), codex["args"])

    def test_codex_config_escapes_paths_and_forwards_only_windows_profile_variables(self):
        with patch("os.name", "nt"), patch("lx.engine.sys.executable", 'C:\\Program Files\\LX "test"\\python.exe'), patch.dict("os.environ", {"USERPROFILE": "C:/Users/someone", "SYSTEMROOT": "C:/Windows", "SECRET_TOKEN": "private"}):
            setup = self.app.agent_setup()
            codex = tomllib.loads(setup["codex_config"])["mcp_servers"]["lx"]
            self.assertEqual(codex["command"], sys.executable)
        self.assertIn("USERPROFILE", codex["env_vars"])
        self.assertIn("SYSTEMROOT", codex["env_vars"])
        self.assertNotIn("SECRET_TOKEN", codex["env_vars"])
        self.assertNotIn("private", setup["codex_config"])

    def test_codex_cli_command_uses_this_install_and_root(self):
        command = self.app.agent_setup()["codex"]
        self.assertTrue(command.startswith("codex mcp add lx -- "))
        self.assertIn("-m lx.mcp_server", command)
        self.assertIn("--root", command)

    def test_muse_code_gets_an_optional_stdio_entry(self):
        entry = json.loads(self.app.system()["agent_setup"]["muse"])
        self.assertEqual(entry["schema_version"], 1)
        server = entry["mcp_servers"]["lx"]
        self.assertEqual((server["transport"], server["mode"], server["command"]), ("stdio", "optional", sys.executable))

    def test_muse_code_is_given_the_variables_a_server_needs_on_windows(self):
        """Muse Code starts a server with only the variables listed; without USERPROFILE the server never answered."""
        with patch("os.name", "nt"), patch.dict("os.environ", {"USERPROFILE": "C:/Users/someone", "SYSTEMROOT": "C:/Windows", "SECRET_TOKEN": "x"}):
            server = json.loads(self.app.agent_setup()["muse"])["mcp_servers"]["lx"]
        self.assertEqual(server["env"]["USERPROFILE"], "C:/Users/someone")
        self.assertIn("SYSTEMROOT", server["env"])
        self.assertNotIn("SECRET_TOKEN", server["env"])

    def test_the_home_folder_is_found_without_the_variables(self):
        from lx import home as home_module
        with patch.object(home_module.Path, "home", side_effect=RuntimeError("no home")):
            if __import__("os").name == "nt":
                self.assertTrue(home_module.home().is_dir())
            else:
                with self.assertRaises(RuntimeError):
                    home_module.home()

    def test_claude_code_gets_a_command_for_this_python(self):
        command = self.app.agent_setup()["claude"]
        self.assertTrue(command.startswith("claude mcp add --transport stdio --scope local lx -- "))
        self.assertIn("-m lx.mcp_server", command)

    def test_a_private_state_folder_is_passed_along(self):
        self.assertIn(str(self.app.root), self.app.agent_setup()["muse"].replace("\\\\", "\\"))

    def test_it_never_holds_a_key(self):
        self.app.registry.update("spark-contributor", api_key="dummy-ui-key")
        self.assertNotIn("dummy-ui-key", str(self.app.agent_setup()))

    def test_it_says_when_the_extra_is_missing(self):
        with patch("lx.engine.find_spec", return_value=None):
            self.assertFalse(self.app.agent_setup()["installed"])


if __name__ == "__main__":
    unittest.main()
