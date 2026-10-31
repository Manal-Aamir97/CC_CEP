"""Tests for the prompt builder and the model clients (no network).

    python -m unittest llm.test_llm -v
"""
import json
import re
import unittest

from app import study
from llm import client as llm_client
from llm.prompt import MAX_HISTORY_TURNS, SYSTEM_PROMPT, build_messages, task_requirements
from validator import validate

RULE_ID = re.compile(r"\b(WS|ST|DB)-\d{2}\b")


class PromptTests(unittest.TestCase):
    def test_structure_follows_the_build_plan(self):
        task = study.public_task("A1")
        prompt = build_messages(task, task["initial_config"], "Is SSH set up right?")
        self.assertEqual(prompt["system"], SYSTEM_PROMPT)
        text = prompt["messages"][-1]["content"]
        for heading in ("TASK REQUIREMENTS:", "CURRENT CONFIGURATION:", "WHAT THE PERSON SEES:", "USER QUESTION:"):
            self.assertIn(heading, text)
        self.assertLess(text.index("TASK REQUIREMENTS:"), text.index("CURRENT CONFIGURATION:"))
        self.assertLess(text.index("WHAT THE PERSON SEES:"), text.index("USER QUESTION:"))
        self.assertTrue(text.endswith("Is SSH set up right?"))
        self.assertIn(json.dumps(task["initial_config"], indent=2), text)
        self.assertIn(task_requirements(task), text)

    def test_prompt_never_carries_findings_or_answers(self):
        for task_id in study.sets()["sets"]["A"] + study.sets()["sets"]["B"]:
            full, public = study.task(task_id), study.public_task(task_id)
            findings = validate(full["initial_config"], full)
            text = json.dumps(build_messages(public, public["initial_config"], "Help?"))
            with self.subTest(task=task_id):
                self.assertIsNone(RULE_ID.search(text))
                for f in findings.findings:
                    self.assertNotIn(f.message, text)
                for value in full["scoring"]["params"].values():
                    in_text = str(value) in " ".join(r["text"] for r in full["participant"]["requirements"])
                    if isinstance(value, str) and not in_text:
                        self.assertNotIn(value, text)
                for trap in full["design"]["traps"]:
                    self.assertNotIn(trap["basis"][:40], text)

    def test_full_task_files_are_refused(self):
        with self.assertRaises(ValueError):
            build_messages(study.task("A1"), {}, "Help?")
        with self.assertRaises(ValueError):
            build_messages(study.public_task("A1"), {}, "   ")

    def test_history_is_capped_and_newest_message_is_structured(self):
        task = study.public_task("A2")
        history = [(f"q{i}", f"a{i}") for i in range(10)]
        prompt = build_messages(task, task["initial_config"], "Next?", history)
        self.assertEqual(len(prompt["messages"]), 2 * MAX_HISTORY_TURNS + 1)
        self.assertEqual(prompt["messages"][0]["content"], "q4")
        self.assertEqual([m["role"] for m in prompt["messages"][:2]], ["user", "assistant"])
        self.assertIn("TASK REQUIREMENTS:", prompt["messages"][-1]["content"])


class ClientTests(unittest.TestCase):
    def test_default_is_unavailable(self):
        client = llm_client.get_client("none")
        with self.assertRaises(llm_client.AssistantUnavailable):
            client.complete({"messages": [{"role": "user", "content": "x"}]})

    def test_stub_is_deterministic_and_labelled(self):
        task = study.public_task("A1")
        prompt = build_messages(task, task["initial_config"], "What about RDP?")
        reply = llm_client.get_client("stub").complete(prompt)
        self.assertIn("not a real AI model", reply.text)
        self.assertIn("What about RDP?", reply.text)
        self.assertEqual((reply.provider, reply.model), ("stub", "stub-v1"))

    def test_unknown_provider_fails_loudly(self):
        with self.assertRaises(ValueError):
            llm_client.get_client("gpt-imaginary")

    def test_settings_follow_the_plan(self):
        info = llm_client.describe(llm_client.get_client("stub"))
        self.assertEqual(info["temperature"], 0)
        self.assertEqual(info["provider"], "stub")


class OfflineCheckTests(unittest.TestCase):
    def test_thirty_planted_cases_each_with_known_errors(self):
        from llm.offline_cases import planted_cases
        cases = planted_cases()
        self.assertEqual(len(cases), 30)
        self.assertEqual(len({c["case_id"] for c in cases}), 30)
        for case in cases:
            with self.subTest(case=case["case_id"]):
                self.assertTrue(case["expected_failures"], "every planted case must contain an error")
                if not case["case_id"].endswith("starting-form"):
                    self.assertEqual(len(case["expected_failures"]), 1)

    def test_dry_run_writes_answers_coding_sheet_and_scores(self):
        import csv
        import shutil
        import tempfile
        from pathlib import Path
        from llm import offline_check
        from llm.offline_cases import planted_cases
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        folder = offline_check.run("stub", "planted", out_root=tmp)
        with open(folder / "answers.jsonl", encoding="utf-8") as f:
            records = [json.loads(line) for line in f]
        self.assertEqual(len(records), 30)
        for record in records:
            prompt_text = json.dumps(record["prompt"])
            self.assertIsNone(RULE_ID.search(prompt_text))       # findings never reach the model
            self.assertTrue(record["findings"])                  # but are stored beside the answer
        with open(folder / "coding.csv", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        errors = sum(len(r["expected_failures"]) for r in records)
        self.assertEqual(sum(1 for r in rows if r["row_type"] == "real error"), errors)
        self.assertEqual(sum(1 for r in rows if r["row_type"] == "wrong or harmful advice"), 30)
        fixed = next(c for c in planted_cases() if c["case_id"] == "planted-A1-disk_not_encrypted")
        after = dict(fixed["config"], disk_encryption=True)
        (folder / "after" / "planted-A1-disk_not_encrypted.json").write_text(json.dumps(after), encoding="utf-8")
        totals = offline_check.score(folder)
        self.assertEqual(totals, {"cases": 1, "before": 1, "after": 0})

    def test_no_model_means_no_run(self):
        from llm import offline_check
        with self.assertRaises(SystemExit):
            offline_check.run("none", "planted")


if __name__ == "__main__":
    unittest.main()
