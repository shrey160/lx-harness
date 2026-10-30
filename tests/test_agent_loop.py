"""The guided agent loop, from what the 31 Oct review found: images, repeats, limits, lost reasoning, broken text."""
import json
import unittest

from lx.agent import (IMAGE_KEY, MAX_WINDOW_IMAGES, WINDOW_CHARS, render_trace, run_agent, run_rules)
from lx.live_transport import MAX_IMAGES_PER_REQUEST
from lx.spark import normalize
from lx.spike import wire_reply


class Profile:
    supports_tools = True


class Client:
    """Plays back replies and keeps a copy of every request's messages."""
    profile = Profile

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def chat(self, messages, **_):
        self.seen.append(json.loads(json.dumps(messages)))
        return self.replies.pop(0), {"output_tokens": 1}


def calls(*pairs):
    return {"content": None, "tool_calls": [{"id": f"c{i}", "name": name, "arguments": args}
                                            for i, (name, args) in enumerate(pairs)]}


def say(text):
    return {"content": text, "tool_calls": []}


def view(pictures):
    """A handler whose views return a distinct image per photo and box."""
    def handle(name, arguments):
        key = f"{arguments['photo']}:{arguments.get('box')}"
        return {"photo": arguments["photo"], IMAGE_KEY: pictures.get(key, key.encode())}
    return handle


def images_in(messages):
    return [part["image_url"]["url"] for m in messages if isinstance(m.get("content"), list)
            for part in m["content"] if part["type"] == "image_url"]


def texts(messages):
    return " ".join(m["content"] if isinstance(m.get("content"), str) else " ".join(p.get("text", "") for p in m.get("content") or [])
                    for m in messages)


START = [{"role": "user", "content": "go"}]
ANSWER = say('{"answer": 1}')


class ImageWindow(unittest.TestCase):
    def run_views(self, *turns, keep=8, pictures=None):
        client = Client(*turns, ANSWER)
        run_agent(client, START, [], view(pictures or {}), max_turns=30, max_tool_calls=60, keep_images=keep, guided=True)
        return client

    def test_every_photo_of_a_seven_photo_sample_stays(self):
        client = self.run_views(*[calls(("view", {"photo": n})) for n in range(1, 8)])
        self.assertEqual(len(images_in(client.seen[-1])), 7)

    def test_the_review_cycle_cannot_happen(self):
        """Photos 1, 2 and 3, 4 alternating: on 31 Oct each turn evicted the last and the model looked again."""
        cycle = [calls(("view", {"photo": a}), ("view", {"photo": b})) for a, b in [(1, 2), (3, 4)]*5]
        client = Client(*cycle, ANSWER)
        answer, trace = run_agent(client, START, [], view({}), max_turns=30, max_tool_calls=60, keep_images=6, guided=True)
        self.assertEqual(answer, ANSWER["content"])
        self.assertEqual(len(images_in(client.seen[-1])), 4)
        repeats = [s for s in trace if s["type"] == "tool" and "already" in s["result"]["image"]]
        self.assertEqual(len(repeats), 16)

    def test_a_repeat_says_so_and_sends_no_copy(self):
        client = self.run_views(calls(("view", {"photo": 1})), calls(("view", {"photo": 1})))
        self.assertEqual(len(images_in(client.seen[-1])), 1)
        self.assertIn("already in this conversation", client.seen[-1][-1]["content"])

    def test_two_new_views_in_one_turn_are_both_attached(self):
        client = self.run_views(calls(("view", {"photo": 1}), ("view", {"photo": 2})))
        self.assertEqual(len(images_in(client.seen[-1])), 2)

    def test_close_ups_leave_first_and_whole_photos_stay(self):
        turns = [calls(("view", {"photo": n})) for n in (1, 2, 3)]
        turns += [calls(("view", {"photo": 1, "box": [0, 0, n, n]})) for n in range(1, 5)]
        client = self.run_views(*turns, keep=4)
        held = images_in(client.seen[-1])
        self.assertEqual(len(held), 4)
        last_text = texts(client.seen[-1])
        self.assertIn("photo 1 box [0, 0, 1, 1]: no longer attached", last_text)
        for photo in (1, 2, 3):
            self.assertNotIn(f"[photo {photo}: no longer", last_text)

    def test_the_window_stays_inside_the_request_size(self):
        big = {f"{n}:None": b"x"*(WINDOW_CHARS//4) for n in range(1, 8)}
        big = {key: value+key.encode() for key, value in big.items()}
        client = self.run_views(*[calls(("view", {"photo": n})) for n in range(1, 8)], pictures=big)
        self.assertLessEqual(sum(len(url) for url in images_in(client.seen[-1])), WINDOW_CHARS)
        self.assertGreaterEqual(len(images_in(client.seen[-1])), 2)

    def test_the_transport_allows_the_window(self):
        self.assertGreaterEqual(MAX_IMAGES_PER_REQUEST, MAX_WINDOW_IMAGES)
        self.assertGreaterEqual(MAX_WINDOW_IMAGES, 7+1)


class Limits(unittest.TestCase):
    def test_the_rules_state_this_runs_numbers(self):
        rules = run_rules(16, 90, images=8)
        for fact in ("16 turns", "90 tool calls", "8 images", "JSON answer"):
            self.assertIn(fact, rules)
        self.assertNotIn("images", run_rules(16, 90))

    def test_it_warns_once_before_the_turns_run_out(self):
        client = Client(*[calls(("view", {"photo": 1}))]*5, ANSWER)
        run_agent(client, START, [], view({}), max_turns=6, max_tool_calls=60, keep_images=4, guided=True)
        warnings = [m for m in client.seen[-1] if m["role"] == "user" and "turns and" in str(m["content"])]
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 turns", warnings[0]["content"])

    def test_too_many_calls_in_a_turn_still_gets_an_answer(self):
        client = Client(calls(*[("view", {"photo": n}) for n in range(1, 7)]), ANSWER)
        answer, trace = run_agent(client, START, [], view({}), max_turns=6, max_tool_calls=4, keep_images=4, guided=True)
        self.assertEqual(answer, ANSWER["content"])
        refused = [s for s in trace if s["type"] == "tool" and "no tool calls left" in s.get("error", "")]
        self.assertEqual(len(refused), 2)
        tool_ids = [m["tool_call_id"] for m in client.seen[-1] if m["role"] == "tool"]
        self.assertEqual(len(tool_ids), 6)  # every call is answered, or a provider refuses the history

    def test_calling_again_after_being_told_stops_the_run(self):
        over = calls(*[("view", {"photo": n}) for n in range(1, 4)])
        client = Client(over, over, ANSWER)
        answer, trace = run_agent(client, START, [], view({}), max_turns=8, max_tool_calls=2, keep_images=4, guided=True)
        self.assertEqual(answer, "")
        self.assertEqual(trace[-1]["type"], "stopped")


class Reasoning(unittest.TestCase):
    def test_reasoning_is_read_under_either_name(self):
        for field in ("reasoning", "reasoning_content"):
            raw = wire_reply(calls=[{"id": "a", "type": "function", "function": {"name": "view", "arguments": "{}"}}])
            raw["choices"][0]["message"][field] = "photo 1 has two pens"
            self.assertEqual(normalize(raw, max_tokens=4096)[0]["reasoning"], "photo 1 has two pens")

    def test_a_provider_without_it_changes_nothing(self):
        self.assertEqual(set(normalize(wire_reply("{}"), max_tokens=4096)[0]), {"content", "tool_calls"})

    def test_it_stays_in_the_history_as_text_and_reaches_the_improver(self):
        first = {**calls(("view", {"photo": 1})), "reasoning": "photo 1 has two pens"}
        client = Client(first, ANSWER)
        answer, trace = run_agent(client, START, [], view({}), keep_images=4, guided=True)
        assistant = next(m for m in client.seen[-1] if m["role"] == "assistant")
        self.assertIn("two pens", assistant["content"])
        self.assertIn("[REASONING]\nphoto 1 has two pens", render_trace(trace, answer))

    def test_written_text_is_not_replaced(self):
        client = Client({**calls(("view", {"photo": 1})), "content": "two pens", "reasoning": "long"}, ANSWER)
        run_agent(client, START, [], view({}), keep_images=4, guided=True)
        self.assertEqual(next(m for m in client.seen[-1] if m["role"] == "assistant")["content"], "two pens")

    def test_long_reasoning_is_cut(self):
        client = Client({**calls(("view", {"photo": 1})), "reasoning": "x"*9000}, ANSWER)
        run_agent(client, START, [], view({}), keep_images=4, guided=True)
        self.assertLess(len(next(m for m in client.seen[-1] if m["role"] == "assistant")["content"]), 1600)


class BrokenText(unittest.TestCase):
    BROKEN = '">\n</atem:invoke>'

    def test_broken_tool_text_gets_another_turn(self):
        client = Client(say(self.BROKEN), ANSWER)
        answer, trace = run_agent(client, START, [], view({}), keep_images=4, guided=True)
        self.assertEqual(answer, ANSWER["content"])
        self.assertIn("nudged", [step["type"] for step in trace])
        self.assertIn("[NUDGED]", render_trace(trace, answer))

    def test_it_is_asked_twice_at_most(self):
        client = Client(*[say(self.BROKEN)]*5)
        answer, _ = run_agent(client, START, [], view({}), keep_images=4, guided=True)
        self.assertEqual(answer, self.BROKEN)
        self.assertEqual(len(client.seen), 3)

    def test_a_fenced_answer_is_an_answer(self):
        fenced = '```json\n{"answer": 1}\n```'
        client = Client(say(fenced))
        self.assertEqual(run_agent(client, START, [], view({}), keep_images=4, guided=True)[0], fenced)


class PausedExperimentsKeepTheirLoop(unittest.TestCase):
    def test_unguided_behaves_as_before(self):
        client = Client(*[calls(("view", {"photo": n})) for n in (1, 2, 3, 1)], say("plain text"))
        answer, trace = run_agent(client, START, [], view({}), max_turns=8, max_tool_calls=12)
        self.assertEqual(answer, "plain text")  # no nudge
        self.assertEqual(len(images_in(client.seen[-1])), 2)
        self.assertNotIn("nudged", [step["type"] for step in trace])
        self.assertNotIn("turns and", texts(client.seen[-1]))

    def test_unguided_stops_at_too_many_calls(self):
        client = Client(calls(*[("view", {"photo": n}) for n in range(1, 7)]))
        self.assertEqual(run_agent(client, START, [], view({}), max_tool_calls=4)[0], "")


if __name__ == "__main__":
    unittest.main()
