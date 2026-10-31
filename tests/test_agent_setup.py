"""Connect an agent: Settings gives ready setup, with this install's paths and no keys."""
import json
import sys
import unittest
from unittest.mock import patch

from test_tasks import Base


class Setup(Base):
    def test_muse_code_gets_an_optional_stdio_entry(self):
        entry = json.loads(self.app.system()["agent_setup"]["muse"])
        self.assertEqual(entry["schema_version"], 1)
        server = entry["mcp_servers"]["lx"]
        self.assertEqual((server["transport"], server["mode"], server["command"]), ("stdio", "optional", sys.executable))

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
