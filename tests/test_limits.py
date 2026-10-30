"""User-set run limits: defaults, bounds and how they reach a run (D-017)."""

import unittest

from lx import limits
from lx.inventory import caps
from lx.spark import ChatClient


class PresetTests(unittest.TestCase):
    def test_spark_keeps_its_numbers_and_others_get_longer_ones(self):
        spark, other = limits.preset(True), limits.preset(False)
        self.assertEqual((spark["call_seconds"], spark["improver_seconds"], spark["run_seconds"], spark["improver_turns"], spark["reply_tokens"]),
                         (90, 180, 900, 12, 4096))
        self.assertEqual((other["call_seconds"], other["improver_seconds"], other["run_seconds"], other["improver_turns"], other["reply_tokens"]),
                         (300, 900, 3600, 30, 8192))

    def test_agent_defaults_leave_room_to_look_at_every_photo(self):
        for spark in (True, False):
            self.assertEqual(limits.preset(spark)["agent_turns_per_photo"], 12)
            self.assertEqual(limits.preset(spark)["agent_tools_per_photo"], 12)

    def test_a_task_gets_its_longer_run_and_call_limits(self):
        task = limits.preset(False, task=True)
        self.assertEqual((task["call_seconds"], task["run_seconds"], task["chat_calls"], task["segment_calls"], task["transcribe_calls"]),
                         (600, 14400, 1400, 600, 60))
        self.assertEqual(limits.preset(False)["chat_calls"], 200)

    def test_every_preset_sits_inside_its_bounds(self):
        for spark in (True, False):
            for task in (True, False):
                limits.check(limits.preset(spark, task))


class CheckTests(unittest.TestCase):
    def test_refuses_unknown_names_and_values_outside_the_bounds(self):
        for bad in ({"nope": 1}, {"improver_turns": 0}, {"improver_turns": 101}, {"run_seconds": 59},
                    {"reply_tokens": 40000}, {"chat_calls": 5001}, {"improver_turns": 1.5},
                    {"improver_turns": True}, {"improver_turns": "12"}):
            with self.subTest(bad), self.assertRaises(ValueError):
                limits.check(bad)
        with self.assertRaises(ValueError):
            limits.check([])

    def test_says_which_limit_and_its_range(self):
        with self.assertRaisesRegex(ValueError, "Improver turns per session must be a whole number from 1 to 100"):
            limits.check({"improver_turns": 500})


class DefaultsTests(unittest.TestCase):
    def test_a_role_field_comes_from_that_roles_provider(self):
        mixed = limits.defaults({"orchestrator": (True, {}), "improver": (True, {}), "worker": (False, {})})
        self.assertEqual(mixed["improver_turns"], 12)
        other = limits.defaults({"orchestrator": (True, {}), "improver": (False, {}), "worker": (True, {})})
        self.assertEqual(other["improver_turns"], 30)

    def test_a_run_field_takes_the_longest_default(self):
        mixed = limits.defaults({"orchestrator": (True, {}), "improver": (True, {}), "worker": (False, {})})
        self.assertEqual((mixed["call_seconds"], mixed["reply_tokens"]), (300, 8192))

    def test_a_provider_entry_overrides_the_preset(self):
        values = limits.defaults({"orchestrator": (False, {}), "improver": (False, {"improver_turns": 50}),
                                  "worker": (False, {"improver_turns": 2, "call_seconds": 500})})
        self.assertEqual((values["improver_turns"], values["call_seconds"]), (50, 500))


class ResolveTests(unittest.TestCase):
    def test_the_users_values_go_on_top_and_a_default_is_not_their_own(self):
        base = limits.preset(False)
        values, own = limits.resolve(base, {"improver_turns": 40, "run_seconds": 3600})
        self.assertEqual(values["improver_turns"], 40)
        self.assertEqual(own, {"improver_turns": 40})

    def test_refuses_a_value_outside_the_bounds(self):
        with self.assertRaises(ValueError):
            limits.resolve(limits.preset(False), {"improver_turns": 0})


class ReachTests(unittest.TestCase):
    def test_caps_follow_the_per_photo_limits(self):
        self.assertEqual(caps(3), {"max_turns": 13, "max_tool_calls": 24})
        self.assertEqual(caps(3, turns_per_photo=6, tools_per_photo=10), {"max_turns": 22, "max_tool_calls": 36})

    def test_the_client_applies_the_call_limit_to_each_attempt(self):
        seen = []
        def transport(body, timeout):
            seen.append(timeout)
            raise RuntimeError("stop")
        client = ChatClient(transport, _Ledger(), call_seconds=45)
        with self.assertRaises(RuntimeError):
            client.chat([{"role": "user", "content": "x"}], timeout=200)
        self.assertEqual(seen, [45])

    def test_the_client_takes_a_reply_length_up_to_the_outer_bound(self):
        ChatClient(None, _Ledger(), max_tokens=32768)
        with self.assertRaises(ValueError):
            ChatClient(None, _Ledger(), max_tokens=32769)


class _Ledger:
    def reserve(self, *_args):
        return 1

    def unknown(self, _reservation, _reason=None):
        pass


if __name__ == "__main__":
    unittest.main()
