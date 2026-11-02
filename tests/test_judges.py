"""Model judges: criteria a chat model answers about each answer, beside the check or in place of one."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from unittest.mock import patch

from lx import judging, sandbox
from lx.budget import BudgetExceeded
from lx.checks import named_checks
from lx.inventory_workflow import InventoryPreviewTransport
from lx.spark import ModelError
from lx.spike import wire_reply
from lx.usage import usage_rows
from lx.worker import execute
from test_orchestration import Actions, WholeFlow
from test_trial import Trials

CRITERIA = {"criteria": [{"name": "counts the asked kind", "question": "Does the answer count only the kind of thing the request names?"}]}


class Criteria(unittest.TestCase):
    def test_criteria_are_cleaned_and_checked(self):
        self.assertEqual(judging.check_written({"criteria": [{"name": " a ", "question": " Is it? "}]}, text_task=False),
                         {"criteria": [{"name": "a", "question": "Is it?"}], "sees_request": False})
        for judge, message in (({}, "1 to 50"), ({"criteria": []}, "1 to 50"), ({"criteria": [{"name": "a"}]}, "name and a question"),
                               ({"criteria": [{"name": "", "question": "q"}]}, "name of 1"), ({"criteria": [{"name": "a", "question": "x"*1001}]}, "question of 1"),
                               ({"criteria": [{"name": "a", "question": "q"}]*2}, "own name"), ({**CRITERIA, "weights": [1]}, "an object with criteria"),
                               ({**CRITERIA, "sees_request": "yes"}, "true or false"), ([], "an object")):
            with self.assertRaisesRegex(ValueError, message):
                judging.check_written(judge, text_task=True)

    def test_only_a_text_task_judge_sees_the_request(self):
        self.assertTrue(judging.check_written({**CRITERIA, "sees_request": True}, text_task=True)["sees_request"])
        with self.assertRaisesRegex(ValueError, "text task"):
            judging.check_written({**CRITERIA, "sees_request": True}, text_task=False)

    def test_a_file_that_is_not_json_says_so(self):
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            judging.read_written("{", text_task=False)


class InTheSpec(Actions):
    def write_judges(self, value):
        folder = Path(self.path)/"eval"
        folder.mkdir(exist_ok=True)
        (folder/"judges.json").write_text(json.dumps(value), encoding="utf-8")

    def test_judges_are_kept_until_changed_or_removed(self):
        self.begin()
        self.lx.spec_draft(self.path, judges=CRITERIA)
        expected = {**CRITERIA, "sees_request": False}
        self.assertEqual(self.lx.spec_show(self.path)["judges"], expected)
        self.lx.spec_draft(self.path, goal="Count what the request asks for.")
        self.assertEqual(self.lx.spec_show(self.path)["judges"], expected)
        self.assertIsNotNone(self.lx.spec_show(self.path)["check"])
        self.lx.spec_draft(self.path, no_judges=True)
        self.assertIsNone(self.lx.spec_show(self.path)["judges"])
        with self.assertRaisesRegex(ValueError, "not both"):
            self.lx.spec_draft(self.path, judges=CRITERIA, no_judges=True)

    def test_judges_can_stand_in_for_a_check(self):
        self.begin()
        self.lx.spec_draft(self.path, no_check=True, judges=CRITERIA)
        shown = self.lx.spec_show(self.path)
        self.assertEqual((shown["check"], shown["judges"]["criteria"]), (None, CRITERIA["criteria"]))

    def test_a_photo_task_judge_cannot_see_the_request(self):
        self.begin()
        with self.assertRaisesRegex(ValueError, "text task"):
            self.lx.spec_draft(self.path, judges={**CRITERIA, "sees_request": True})

    def test_the_judges_file_is_taken_and_drift_blocks_until_settled(self):
        self.begin()
        self.write_judges(CRITERIA)
        self.assertEqual(self.lx.spec_show(self.path)["eval_drift"], ["eval/judges.json is not in use, because this spec has no judges"])
        self.lx.spec_draft(self.path, use_judge_file=True)
        self.assertEqual(self.lx.spec_show(self.path)["eval_drift"], [])
        self.write_judges({"criteria": [{"name": "other", "question": "Is it tidy?"}]})
        self.assertEqual(self.lx.spec_show(self.path)["eval_drift"], ["eval/judges.json differs from the spec's judges"])
        self.assertIn("judges.json differs", str(self.lx.ready(self.path)["problems"]))
        with self.assertRaisesRegex(ValueError, "not both"):
            self.lx.spec_draft(self.path, judges=CRITERIA, use_judge_file=True)

    def test_a_missing_judges_file_is_named(self):
        self.begin()
        with self.assertRaisesRegex(ValueError, "eval/judges.json"):
            self.lx.spec_draft(self.path, use_judge_file=True)

    def test_the_judges_file_is_not_a_sample(self):
        self.begin()
        self.write_judges(CRITERIA)
        self.assertNotIn("eval/judges.json", {item["path"] for item in self.lx.samples(self.path)["inventory"]})


class ModelAndCap(Actions):
    def test_a_judge_is_blocked_until_it_has_a_model_and_a_cap(self):
        project = self.begin()
        self.lx.spec_draft(self.path, judges=CRITERIA)
        self.assertEqual(self.app.project(project["id"])["blocked"], "Choose a model for the judges.")
        self.app.edit_spec(project["id"], {"judge": {"entry": "spark-contributor"}})
        self.assertEqual(self.app.project(project["id"])["blocked"], "Set the most the judges may spend on one run.")
        self.app.edit_spec(project["id"], {"judge": {"cap_usd": 0.05}})
        self.assertIn("Raise it to at least $0.11", self.app.project(project["id"])["blocked"])
        self.app.edit_spec(project["id"], {"judge": {"cap_usd": 0.5}})
        project = self.app.project(project["id"])
        self.assertIsNone(project["blocked"])
        judge = project["draft"]["judge"]
        self.assertEqual((judge["entry"], judge["cap_usd"], judge["provider"]["id"]), ("spark-contributor", 0.5, "spark-contributor"))
        self.assertEqual(judge["criteria"], CRITERIA["criteria"])

    def test_the_cap_and_fields_are_checked(self):
        project = self.begin()
        for given, message in (({**CRITERIA, "cap_usd": 0}, "more than"), ({**CRITERIA, "cap_usd": True}, "more than"),
                               ({**CRITERIA, "cap_usd": 21}, "more than"), ({**CRITERIA, "model": "x"}, "take criteria"),
                               ({**CRITERIA, "entry": "no-such-entry"}, "chat provider")):
            with self.assertRaisesRegex(ValueError, message):
                self.app.edit_spec(project["id"], {"judge": given})

    def test_the_judge_is_named_in_the_data_notice(self):
        project = self.begin()
        self.app.edit_spec(project["id"], {"judge": {**CRITERIA, "entry": "spark-contributor", "cap_usd": 0.5}})
        entry = next(item for item in self.app.project(project["id"])["notice"] if item["who"] == "judge")
        self.assertIn("expected answer", entry["gets"])
        self.assertNotIn("request", entry["gets"])

    def test_a_changed_judge_provider_needs_a_new_approval(self):
        project = self.begin()
        self.app.edit_spec(project["id"], {"judge": {**CRITERIA, "entry": "spark-contributor", "cap_usd": 0.5}})
        draft = self.app.project(project["id"])["draft"]
        self.app._check_providers(draft)
        moved = {**draft, "judge": {**draft["judge"], "provider": {**draft["judge"]["provider"], "base_url": "https://elsewhere.example/v1"}}}
        with self.assertRaisesRegex(ValueError, "approve again"):
            self.app._check_providers(moved)


JUDGE = {**CRITERIA, "sees_request": False, "entry": "spark-contributor", "cap_usd": 0.05,
         "provider": {"model_id": "muse-spark", "base_url": "https://example.test/v1"}}


class Client:
    """Replies in turn from `replies`; an exception in the list is raised."""
    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []

    def chat(self, messages, **options):
        self.sent.append((messages, options))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"content": reply}, {}


def verdict(passed=True, name="counts the asked kind"):
    return json.dumps({"verdicts": [{"name": name, "passed": passed, "reason": "because"}]})


class Judging(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.cache.cleanup)

    def judge(self, *replies, judge=JUDGE):
        client = Client(*replies)
        return judging.Judge(judge, client, self.cache.name, live=True), client

    def test_a_verdict_is_read_and_kept_so_asking_again_is_free(self):
        judge, client = self.judge(verdict(False))
        found = judge.verdicts({"total": 3}, {"total": 4})
        self.assertEqual(found, [{"name": "counts the asked kind", "passed": False, "reason": "because"}])
        self.assertEqual(judge.verdicts({"total": 3}, {"total": 4}), found)
        self.assertEqual(len(client.sent), 1)
        self.assertEqual(client.sent[0][1]["role"], "judge")
        self.assertEqual(judging.as_checks(found), [{"name": "judge: counts the asked kind", "passed": False, "error": "judge: counts the asked kind: not met"}])

    def test_the_judge_sees_the_truth_only_when_there_is_one_and_the_request_only_when_allowed(self):
        judge, client = self.judge(verdict(), verdict())
        judge.verdicts(None, "an answer", "the request")
        shown = json.loads(client.sent[0][0][1]["content"])
        self.assertEqual(set(shown), {"criteria", "answer"})
        judge.judge = {**JUDGE, "sees_request": True}
        judge.verdicts({"total": 3}, "an answer", "the request")
        self.assertEqual(json.loads(client.sent[1][0][1]["content"])["request"], "the request")

    def test_a_fenced_reply_is_read(self):
        judge, _ = self.judge("```json\n"+verdict()+"\n```")
        self.assertTrue(judge.verdicts(1, 2)[0]["passed"])

    def test_an_unreadable_reply_is_retried_once_then_not_judged(self):
        judge, client = self.judge("no", verdict())
        self.assertTrue(judge.verdicts(1, 2)[0]["passed"])
        judge, client = self.judge("no", verdict(name="another"), verdict())
        with self.assertRaisesRegex(judging.NotJudged, "criterion's name"):
            judge.verdicts(1, 3)
        self.assertEqual(len(client.sent), 2)

    def test_a_failed_call_is_not_judged_and_a_spent_cap_stops_calling(self):
        judge, _ = self.judge(ModelError("timed out"))
        with self.assertRaisesRegex(judging.NotJudged, "call failed"):
            judge.verdicts(1, 2)
        judge, client = self.judge(BudgetExceeded("full"))
        for answer in (2, 3):
            with self.assertRaisesRegex(judging.NotJudged, "cap was reached"):
                judge.verdicts(1, answer)
        self.assertEqual(len(client.sent), 1)

    def test_verdicts_must_cover_every_criterion(self):
        two = [{"name": "a", "question": "?"}, {"name": "b", "question": "?"}]
        with self.assertRaisesRegex(judging.NotJudged, "one verdict for each"):
            judging.read_verdicts(json.dumps({"verdicts": [{"name": "a", "passed": True}]}), two)
        for reply in ("[]", '{"verdicts": 1}', '{"verdicts": [{"name": "a", "passed": "yes"}]}'):
            with self.assertRaises(judging.NotJudged):
                judging.read_verdicts(reply, two)

    def test_a_cached_verdict_depends_on_everything_the_judge_saw(self):
        key = lambda **change: judging.cache_key({**JUDGE, **change.pop("judge", {})}, *[change.get(k, 1) for k in ("truth", "answer", "request")], live=change.get("live", True))
        self.assertEqual(len({key(), key(truth=2), key(answer=2), key(request=2), key(live=False),
                              key(judge={"criteria": [{"name": "x", "question": "?"}]})}), 6)

    def test_the_preview_double_passes_answers_that_are_not_empty(self):
        transport = judging.JudgePreviewTransport()
        ask = lambda answer: json.loads(transport({"messages": [{"role": "developer", "content": judging.INSTRUCTIONS},
            {"role": "user", "content": judging._question(CRITERIA["criteria"], None, answer, None)}]}, 1)["choices"][0]["message"]["content"])
        self.assertTrue(ask({"total": 1})["verdicts"][0]["passed"])
        self.assertFalse(ask({})["verdicts"][0]["passed"])


READY = {**CRITERIA, "entry": "spark-contributor", "cap_usd": 0.5}


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class JudgedRuns(Actions):
    preview, improver_input = WholeFlow.preview, WholeFlow.improver_input

    def judged(self, **draft):
        project = self.begin()
        self.lx.spec_draft(self.path, judges=READY, **draft)
        workspace = self.app.workspace(project["id"])
        return project, workspace, self.preview(workspace)

    def test_a_judged_run_counts_the_judge_beside_the_check(self):
        project, workspace, run_id = self.judged()
        result = self.lx.results(self.path, run_id)
        self.assertEqual((result["scored"], result["not_graded"]), (True, 0))
        self.assertIn("model judge", result["grading"])
        self.assertIsNotNone(result["means"]["base"])
        report = json.loads((workspace.run_dir(run_id)/"outputs"/"ea_report.json").read_text(encoding="utf-8"))
        sample, row = next(iter(report["per_sample"].items()))
        run_dir = workspace.run_dir(run_id)
        answer = json.loads((run_dir/"outputs"/"runs"/row["reply_files"]["base"]/"answer.json").read_text(encoding="utf-8"))
        truth = json.loads((run_dir/"dataset"/"controller_truth"/"ground_truth.json").read_text(encoding="utf-8"))[sample]
        check = workspace.check_source(self.app.project(project["id"])["draft"]["check"])
        self.assertEqual(row["base"]["checks"], len(named_checks(check, truth, answer))+1)
        verdicts = json.loads((workspace.run_dir(run_id)/"outputs"/"judged.json").read_text(encoding="utf-8"))
        self.assertEqual(len(verdicts), 12)
        self.assertEqual(next(iter(verdicts.values()))[0]["name"], "counts the asked kind")
        package = workspace.package_dir(run_id)
        self.assertEqual(json.loads((package/"judges.json").read_text(encoding="utf-8"))["criteria"], CRITERIA["criteria"])
        self.assertIn("Partly judged by", (package/"limitations.md").read_text(encoding="utf-8"))
        rows = usage_rows(workspace.preview_ledger, run_id+"/judge/")
        self.assertEqual({row["role"] for row in rows}, {"judge"})
        self.assertEqual({row["phase"] for row in rows}, {"grading"})

    def test_judges_alone_score_a_run_with_no_check(self):
        _, _, run_id = self.judged(no_check=True)
        result = self.lx.results(self.path, run_id)
        self.assertEqual((result["scored"], result["means"]), (True, {"base": 1.0, "harness2": 1.0}))

    def test_judges_change_nothing_the_loop_sees_or_learns(self):
        project = self.begin()
        workspace = self.app.workspace(project["id"])
        with patch("lx.taskrun.run_order", lambda stream, identifier: sorted(stream)):
            plain = self.preview(workspace)
            self.lx.spec_draft(self.path, judges=READY)
            judged = self.preview(workspace)
        files = [{path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
                 for root in (workspace.package_dir(run)/"harness" for run in (plain, judged))]
        self.assertEqual(files[0], files[1])
        first, second = self.improver_input(workspace, plain), self.improver_input(workspace, judged)
        self.assertTrue(first)
        self.assertEqual(sorted(first), sorted(second))
        self.assertEqual([name for name in first if first[name] != second[name]], [])
        self.assertFalse(any("LX_JUDGE" in text or "counts the asked kind" in text for text in second.values()))

    def test_answers_the_judge_could_not_judge_are_left_out_not_zeroed(self):
        broken = lambda self, body, timeout: wire_reply("PREFLIGHT_OK" if body["messages"][0]["content"].startswith("LX_PREFLIGHT") else "I cannot say")
        with patch.object(judging.JudgePreviewTransport, "__call__", broken):
            _, workspace, run_id = self.judged()
        result = self.lx.results(self.path, run_id)
        self.assertEqual((result["means"], result["not_graded"]), ({"base": None, "harness2": None}, 12))
        self.assertIn("not judged", result["learned"])
        self.assertIn("12 answer(s) were not judged", (workspace.package_dir(run_id)/"limitations.md").read_text(encoding="utf-8"))
        verdicts = json.loads((workspace.run_dir(run_id)/"outputs"/"judged.json").read_text(encoding="utf-8"))
        self.assertIn("not JSON", next(iter(verdicts.values()))["not_judged"])

    def test_a_regrade_reuses_kept_verdicts_and_makes_no_call(self):
        project, workspace, run_id = self.judged()
        before = self.lx.results(self.path, run_id)["means"]
        workspace.update_run(run_id, status="failed")
        with patch.object(judging.JudgePreviewTransport, "__call__", lambda *args: self.fail("a regrade called the judge")):
            self.app.regrade(project["id"], run_id)
        self.assertEqual(workspace.run(run_id)["result"]["means"], before)


@unittest.skipUnless(sandbox.available(), "needs the script sandbox")
class JudgedTrial(Trials):
    def test_a_trial_counts_the_judge_and_says_when_it_could_not(self):
        loop, judge = InventoryPreviewTransport(), judging.JudgePreviewTransport()
        self.transport = lambda body, timeout: (judge if body["messages"][0]["content"].startswith(judging.MARKER) else loop)(body, timeout)
        self.app._live_transport_factory = lambda hint=None: self.transport
        self.lx.spec_draft(self.path, judges=READY)
        _, result = self.trial()
        self.assertTrue(result["scored"])
        self.assertIn("judge: counts the asked kind", {item["name"] for item in result["checks"]})
        self.assertEqual(set(result["checks"][0]), {"name", "passed"})
        judge = lambda body, timeout: wire_reply("PREFLIGHT_OK" if body["messages"][0]["content"].startswith("LX_PREFLIGHT") else "nope")
        shutil.rmtree(self.workspace.home/"judge-cache")
        _, result = self.trial()
        self.assertIsNone(result["score"])
        self.assertIn("not JSON", result["not_judged"])


if __name__ == "__main__":
    unittest.main()
