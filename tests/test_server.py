"""Loopback HTTP boundary tests for the controlled preview application."""

import http.client
import gc
import os
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from lx.app import Application
from lx.server import server_for


class ServerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(dir=None if os.name == "posix" else Path(__file__).resolve().parents[1])
        self.addCleanup(self.tempdir.cleanup)
        self.app = Application(Path(self.tempdir.name) / "app-root")
        self.assertIsNone(self.app.live_ledger)
        self.server = server_for(self.app, 0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(gc.collect)
        self.addCleanup(self._stop_server)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        status, session, _ = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.token = session["token"]
        self.assertEqual(session["mode"], "controlled_preview")

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

    def test_state_defaults_to_preview_with_gated_live_and_no_secret_metadata(self):
        status, state, _ = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["mode"], "controlled_preview")
        self.assertTrue(state["live_application_enabled"])
        self.assertEqual(state["live_flow"], "approval_gated_spark_only")
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
            {"synthetic_public": True, "source": "demo", "name": "Boundary test", "goal": "Annotate demo"},
            token=self.token,
        )
        self.assertEqual(status, 200, project)
        self.assertEqual(project["name"], "Boundary test")
        self.assertIsNone(project["approved"])
        self.assertTrue(project["compatible_demo"])
        self.project_id = project["id"]

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
            {"synthetic_public": True, "source": "demo", "name": "Model boundary", "goal": "Annotate demo"},
            token=self.token,
        )
        self.assertEqual(status, 200)
        project_id = project["id"]
        status, rejected, _ = self.request(
            "POST", f"/api/projects/{project_id}/spec", {"model": "sam-catalog"}, token=self.token
        )
        self.assertEqual(status, 400)
        self.assertIn("Only Spark Contributor", rejected["error"])

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
            {"synthetic_public": True, "source": "demo", "name": "Approval boundary", "goal": "Annotate demo"},
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
