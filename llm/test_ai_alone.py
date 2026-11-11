"""Tests for the AI-alone runs (no network).

    python -m unittest llm.test_ai_alone -v
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from app import study
from llm import ai_alone
from llm import client as llm_client
from validator import validate

RULE_ID = re.compile(r"\b(WS|ST|DB)-\d{2}\b")


def starting_form_failures(task):
    """A starting form fails its traps plus its decision points (fields left empty on purpose)."""
    design = task["design"]
    return sorted(d["rule"] for d in design["traps"] + design.get("decision_points", []))


class FakeClient:
    """Answers with a fixed text; raises RateLimited after `limit` answers."""
    provider = "fake"
    model = "fake-model"
    configured = True

    def __init__(self, answer, limit=None):
        self.answer, self.limit, self.calls = answer, limit, 0

    def complete(self, prompt):
        if self.limit is not None and self.calls >= self.limit:
            raise llm_client.RateLimited("limit reached")
        self.calls += 1
        return llm_client.Reply(self.answer(prompt) if callable(self.answer) else self.answer, "fake", "fake-model", 1)


class PromptTests(unittest.TestCase):
    def test_prompt_never_carries_findings_rules_or_answers(self):
        for task_id in ai_alone.task_ids():
            full = study.task(task_id)
            text = json.dumps(ai_alone.build_prompt(study.public_task(task_id)))
            with self.subTest(task=task_id):
                self.assertIsNone(RULE_ID.search(text))
                for finding in validate(full["initial_config"], full).findings:
                    self.assertNotIn(finding.message, text)
                for value in full["scoring"]["params"].values():
                    in_text = str(value) in " ".join(r["text"] for r in full["participant"]["requirements"])
                    if isinstance(value, str) and not in_text:
                        self.assertNotIn(value, text)
                for trap in full["design"]["traps"]:
                    self.assertNotIn(trap["basis"][:40], text)
                self.assertNotIn("reference_solution", text)
                self.assertNotIn("trap", text.lower())

    def test_prompt_holds_the_technical_task_and_starting_form(self):
        task = study.public_task("A1")
        text = ai_alone.build_prompt(task)["messages"][-1]["content"]
        self.assertIn(task["participant"]["requirements"][0]["text"], text)
        self.assertIn(json.dumps(task["initial_config"], indent=2), text)

    def test_refuses_private_task_data(self):
        with self.assertRaises(ValueError):
            ai_alone.build_prompt(study.task("A1"))

    def test_allowed_values_have_no_guidance_text(self):
        for resource in ("web_server", "storage_bucket", "database"):
            text = json.dumps(ai_alone.allowed_values(resource))
            with self.subTest(resource=resource):
                self.assertNotIn("description", text)
                self.assertNotIn("$ref", text)
                self.assertIn('"additionalProperties": false', text)


class ParseAndScoreTests(unittest.TestCase):
    def test_reads_json_in_fences_or_prose(self):
        cfg = {"resource": "web_server", "x": [1, {"y": 2}]}
        for answer in (json.dumps(cfg), "```json\n" + json.dumps(cfg) + "\n```",
                       "Here it is: " + json.dumps(cfg) + " Done.", "{not json} then " + json.dumps(cfg)):
            with self.subTest(answer=answer[:20]):
                self.assertEqual(ai_alone.parse_config(answer), cfg)

    def test_no_json_is_unreadable(self):
        config, problem, result = ai_alone.score_answer("A1", "I cannot do that.")
        self.assertIsNone(config)
        self.assertTrue(problem.startswith("unreadable"))
        self.assertIsNone(result)

    def test_reference_solution_scores_zero_and_starting_form_fails_its_traps(self):
        for task_id in ai_alone.task_ids():
            full = study.task(task_id)
            with self.subTest(task=task_id):
                _, problem, result = ai_alone.score_answer(task_id, json.dumps(full["reference_solution"]))
                self.assertIsNone(problem)
                self.assertEqual(result["error_count"], 0)
                _, _, start = ai_alone.score_answer(task_id, json.dumps(full["initial_config"]))
                self.assertEqual(sorted(start["failed_rules"]), starting_form_failures(full))

    def test_schema_breaking_config_is_invalid_not_scored(self):
        bad = dict(study.task("A1")["initial_config"], disk_encryption="yes")
        config, problem, result = ai_alone.score_answer("A1", json.dumps(bad))
        self.assertEqual(config, bad)
        self.assertTrue(problem.startswith("invalid"))
        self.assertIsNone(result)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _records(self, folder):
        return [json.loads(line) for line in (folder / "answers.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_stub_dry_run_scores_every_task_and_writes_summary(self):
        folder = ai_alone.run("stub", 2, out_root=self.root)
        records = self._records(folder)
        self.assertEqual(len(records), 12)
        for r in records:
            full = study.task(r["task_id"])
            self.assertEqual(r["config"], full["initial_config"])
            self.assertEqual(r["result"]["error_count"], len(starting_form_failures(full)))
        summary = (folder / "summary.md").read_text(encoding="utf-8")
        self.assertIn("| A1 | A | web_server | 2 | 2 |", summary)
        self.assertIn("Usable answers: 12 / 12 (100%)", summary)

    def test_rate_limit_keeps_results_and_resume_finishes(self):
        reference = lambda prompt: json.dumps(next(study.task(t)["reference_solution"] for t in ai_alone.task_ids()
                                                   if study.task(t)["participant"]["title"] in prompt["messages"][-1]["content"]))
        original = ai_alone.get_client
        try:
            ai_alone.get_client = lambda provider: FakeClient(reference, limit=4)
            folder = ai_alone.run("fake", 1, out_root=self.root)
            self.assertEqual(len(self._records(folder)), 4)
            ai_alone.get_client = lambda provider: FakeClient(reference)
            ai_alone.run("fake", 1, out_root=self.root, resume=folder)
        finally:
            ai_alone.get_client = original
        records = self._records(folder)
        self.assertEqual(sorted(r["task_id"] for r in records), sorted(ai_alone.task_ids()))
        self.assertTrue(all(r["result"]["error_count"] == 0 for r in records))

    def test_runs_are_spread_across_tasks_first(self):
        original = ai_alone.get_client
        try:
            ai_alone.get_client = lambda provider: FakeClient("no json here", limit=6)
            folder = ai_alone.run("fake", 3, out_root=self.root)
        finally:
            ai_alone.get_client = original
        records = self._records(folder)
        self.assertEqual({r["run"] for r in records}, {1})
        self.assertEqual(sorted(r["task_id"] for r in records), sorted(ai_alone.task_ids()))
        self.assertIn("Usable answers: 0 / 6 (0%)", (folder / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
