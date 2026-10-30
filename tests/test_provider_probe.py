from pathlib import Path
import tempfile
import unittest

from lx.budget import BudgetLedger
from lx.provider_probe import run
from lx.spark import ModelError
from lx.spike import wire_reply


class ProviderProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root/"demo.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.ledger = BudgetLedger(self.root/"ledger.sqlite3", 1, 0)

    def test_all_three_calls_run_and_record_the_reply_shape(self):
        seen = []
        def transport(body, timeout):
            seen.append(body)
            return wire_reply("fine")
        results = run(transport, self.ledger, self.image)
        self.assertEqual([r["call"] for r in results], ["text", "tool", "image"])
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(results[0]["reply"]["finish_reason"], "stop")
        self.assertEqual(seen[0]["max_tokens"], 8192)
        self.assertIn("tools", seen[1])

    def test_the_first_failure_stops_the_probe(self):
        calls = []
        def transport(body, timeout):
            calls.append(body)
            raise ModelError("provider request failed (HTTP 400); response body withheld")
        results = run(transport, self.ledger, self.image)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["ok"])
        self.assertIn("HTTP 400", results[0]["error"])


if __name__ == "__main__":
    unittest.main()
