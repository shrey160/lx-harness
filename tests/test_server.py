"""Loopback HTTP boundary tests for the local app."""

import http.client
import gc
import os
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
import uuid

from lx.engine import Engine
from lx.server import server_for


class MemoryKeychain:
    """Never the real Credential Manager in tests."""
    def __init__(self, usable=True):
        self.values, self.usable = {}, usable
    def available(self): return self.usable
    def set(self, name, value): self.values[name] = value
    def get(self, name): return self.values.get(name)
    def delete(self, name): self.values.pop(name, None)


class ServerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=None if os.name == "posix" else Path(__file__).resolve().parents[1])
        self.addCleanup(self.tempdir.cleanup)
        self.keychain = MemoryKeychain()
        self.app = Engine(Path(self.tempdir.name) / "app-root", keychain=self.keychain)
        self.assertIsNone(self.app.pilot_ledger)
        self.server = server_for(self.app, 0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(gc.collect)
        self.addCleanup(self._stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        status, session, _ = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.token = session["token"]
        self.assertNotIn("mode", session)

    def body(self, name):
        folder = Path(self.tempdir.name) / uuid.uuid4().hex
        folder.mkdir()
        return {"synthetic_public": True, "source": "demo", "name": name, "goal": "Annotate demo", "path": str(folder)}

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, data=None, *, token=None, origin=None, content_type="application/json"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        body = None if data is None else (data if isinstance(data, bytes) else json.dumps(data).encode())
        headers = {}
        if body is not None:
            headers["Content-Type"] = content_type
        if token is not None:
            headers["X-LX-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        status = response.status
        response_headers = dict(response.getheaders())
        connection.close()
        try:
            value = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            value = payload
        return status, value, response_headers

    def test_system_reports_sandbox_and_agent_tools(self):
        status, system, _ = self.request("GET", "/api/system")
        self.assertEqual(status, 200)
        self.assertIn(system["sandbox"], {"docker", "linux-namespace", None})
        self.assertLessEqual({"view", "segment", "run_script"}, {tool["name"] for tool in system["agent_tools"]})

    def test_state_has_no_secret_metadata(self):
        status, state, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertIsNone(state["live_usage"])
        serialized = json.dumps(state)
        self.assertNotIn("secret_ref", serialized)
        self.assertNotIn("secret_path", serialized)
        self.assertNotIn(str(self.app.root / "secrets"), serialized)

    def test_mutation_requires_session_token(self):
        status, _, _ = self.request(
            "POST", "/api/projects", {"synthetic_public": True, "source": "demo"}
        )
        self.assertEqual(status, 403)

    def test_workspace_lifecycle_routes_require_token_and_allow_one_click_delete(self):
        status, p, _ = self.request("POST", "/api/projects", self.body("Disposable HTTP workspace"), token=self.token)
        self.assertEqual(status, 200, p)
        route = f"/api/projects/{p['id']}"
        for action in ("archive", "restore", "delete"):
            status, _, _ = self.request("POST", route+"/"+action, {"confirmation": p["name"]})
            self.assertEqual(status, 403)
        status, archived, _ = self.request("POST", route+"/archive", {}, token=self.token)
        self.assertEqual(status, 200, archived)
        self.assertTrue(archived["archived"])
        status, restored, _ = self.request("POST", route+"/restore", {}, token=self.token)
        self.assertEqual(status, 200, restored)
        self.assertFalse(restored["archived"])
        status, _, _ = self.request("POST", route+"/delete", {"confirmation": "wrong"}, token=self.token)
        self.assertEqual(status, 400)
        status, result, _ = self.request("POST", route+"/delete", {}, token=self.token)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["deleted"])
        self.assertTrue((Path(p["path"])/"samples"/"manifest.json").exists())

    def test_agent_deletion_setting_requires_session_token_and_a_boolean(self):
        status, _, _ = self.request("POST", "/api/agent-workspace-deletion", {"on": True})
        self.assertEqual(status, 403)
        self.assertFalse(self.app.agent_workspace_deletion())
        status, _, _ = self.request("POST", "/api/agent-workspace-deletion", {"on": "true"}, token=self.token)
        self.assertEqual(status, 400)
        status, result, _ = self.request("POST", "/api/agent-workspace-deletion", {"on": True}, token=self.token)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["agent_workspace_deletion"])
        status, result, _ = self.request("POST", "/api/agent-workspace-deletion", {"on": False}, token=self.token)
        self.assertEqual(status, 200, result)
        self.assertFalse(result["agent_workspace_deletion"])

    def test_a_rejected_post_still_gets_its_reply_with_a_large_body(self):
        # Unread bytes at close reset the connection on Windows (WinError 10053).
        status, _, _ = self.request("POST", "/api/projects", b"x"*(16*1024*1024))
        self.assertEqual(status, 403)

    def test_invalid_host_is_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.putrequest("GET", "/api/session", skip_host=True)
        connection.putheader("Host", "evil.example")
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()

    def test_cross_origin_post_is_rejected(self):
        status, _, _ = self.request(
            "POST", "/api/projects", {"synthetic_public": True, "source": "demo"},
            token=self.token, origin="http://evil.example",
        )
        self.assertEqual(status, 403)

    def test_non_json_post_is_rejected(self):
        status, _, _ = self.request(
            "POST", "/api/projects", b"synthetic_public=true", token=self.token,
            content_type="text/plain",
        )
        self.assertEqual(status, 415)

    def test_create_project_requires_and_accepts_demo_data_consent(self):
        status, project, _ = self.request(
            "POST", "/api/projects",
            self.body("Boundary test"),
            token=self.token,
        )
        self.assertEqual(status, 200, project)
        self.assertEqual(project["name"], "Boundary test")
        self.assertIsNone(project["approved"])
        self.assertTrue(project["compatible_demo"])
        self.assertTrue((Path(project["path"]) / ".lx" / "project.json").is_file())
        self.assertTrue((Path(project["path"]) / "samples" / "manifest.json").is_file())

    def test_existing_workspace_opens_and_a_plain_folder_does_not(self):
        body = self.body("Reopened")
        status, project, _ = self.request("POST", "/api/projects", body, token=self.token)
        self.assertEqual(status, 200, project)
        status, opened, _ = self.request("POST", "/api/workspaces/open", {"path": body["path"]}, token=self.token)
        self.assertEqual(status, 200, opened)
        self.assertEqual(opened["id"], project["id"])
        status, _, _ = self.request("POST", "/api/projects", body, token=self.token)
        self.assertEqual(status, 400)
        status, refused, _ = self.request("POST", "/api/workspaces/open", {"path": self.body("x")["path"]}, token=self.token)
        self.assertEqual(status, 400)
        self.assertIn("not a workspace", refused["error"])

    def test_model_key_never_appears_in_response_or_state_and_spec_rejects_catalog(self):
        dummy = "dummy-key-never-render-this"
        status, model, _ = self.request(
            "POST", "/api/models",
            {"name": "Local model", "model_id": "test/local", "capabilities": ["classify"], "api_key": dummy},
            token=self.token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(model["has_key"])
        self.assertNotIn(dummy, json.dumps(model))
        self.assertNotIn("api_key", json.dumps(model))
        model_id = model["id"]
        status, updated, _ = self.request(
            "POST", f"/api/models/{model_id}/update", {"api_key": "dummy-rotated-key"}, token=self.token
        )
        self.assertEqual(status, 200)
        self.assertNotIn("dummy-rotated-key", json.dumps(updated))
        self.assertNotIn("api_key", json.dumps(updated))
        status, project, _ = self.request(
            "POST", "/api/projects",
            self.body("Model boundary"),
            token=self.token,
        )
        self.assertEqual(status, 200)
        project_id = project["id"]
        status, rejected, _ = self.request(
            "POST", f"/api/projects/{project_id}/spec",
            {"roles": {"orchestrator": "sam-catalog", "improver": "spark-contributor", "worker": "spark-contributor"}},
            token=self.token,
        )
        self.assertEqual(status, 400)
        self.assertIn("chat provider", rejected["error"])
        status, budgeted, _ = self.request("POST", f"/api/projects/{project_id}/budget", {"cap_usd": 3}, token=self.token)
        self.assertEqual(status, 200, budgeted)
        self.assertEqual(budgeted["budget"]["cap_usd"], 3)
        self.assertEqual(list(self.keychain.values.values()), ["dummy-rotated-key"])

        status, state, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        serialized = json.dumps(state)
        self.assertNotIn(dummy, serialized)
        self.assertNotIn("dummy-rotated-key", serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("secret_ref", serialized)
        self.assertNotIn("secret_path", serialized)
        self.assertNotIn(str(self.app.root / "secrets"), serialized)

    def test_run_is_refused_before_approval(self):
        status, project, _ = self.request(
            "POST", "/api/projects",
            self.body("Approval boundary"),
            token=self.token,
        )
        self.assertEqual(status, 200)
        status, response, _ = self.request(
            "POST", f"/api/projects/{project['id']}/run", {}, token=self.token
        )
        self.assertEqual(status, 400)
        self.assertIn("Approve the current spec", response["error"])


if __name__ == "__main__":
    unittest.main()
