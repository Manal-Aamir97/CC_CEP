"""Tests for the OpenRouter client, against a fake local server (no key, no real network).

    python -m unittest llm.test_openrouter -v
"""
import csv
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from app import study
from app.app import create_app
from llm import client as llm_client, offline_check
from llm.client import AssistantUnavailable, OpenRouterClient, RateLimited
from llm.prompt import SYSTEM_PROMPT, build_messages

SECRET = "sk-or-v1-test-secret-value"
GOOD = {"id": "gen-1", "model": "nvidia/nemotron-3-ultra-550b-a55b-20260604:free",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": " Restrict SSH. "}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30,
                  "completion_tokens_details": {"reasoning_tokens": 12}}}


class FakeOpenRouter(BaseHTTPRequestHandler):
    def do_GET(self):
        """Only reached if a client wrongly follows a redirect."""
        self.server.requests.append({"path": self.path, "headers": dict(self.headers), "body": None})
        raw = json.dumps(GOOD).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        server = self.server
        length = int(self.headers.get("Content-Length", 0))
        server.requests.append({"path": self.path, "headers": dict(self.headers),
                                "body": json.loads(self.rfile.read(length))})
        status, body, headers = server.script.pop(0) if server.script else (200, GOOD, {})
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenRouter)
        self.server.requests, self.server.script = [], []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/chat"
        self.client = OpenRouterClient(api_key=SECRET, model="test/model:free", url=self.url, retry_wait_s=0)
        task = study.public_task("A1")
        self.prompt = build_messages(task, task["initial_config"], "Is SSH safe?")


class RequestTests(ServerTest):
    def test_request_follows_the_study_settings(self):
        self.client.complete(self.prompt)
        sent = self.server.requests[0]
        self.assertEqual(sent["headers"]["Authorization"], f"Bearer {SECRET}")
        body = sent["body"]
        self.assertEqual(body["model"], "test/model:free")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["max_tokens"], llm_client.SETTINGS["max_output_tokens"])
        self.assertEqual(body["seed"], llm_client.SETTINGS["seed"])
        self.assertEqual(body["reasoning"], {"effort": "low", "exclude": True})
        self.assertEqual(body["provider"], {"allow_fallbacks": False, "require_parameters": True})
        self.assertFalse(body["stream"])
        self.assertEqual(body["messages"][0], {"role": "system", "content": SYSTEM_PROMPT})
        self.assertIn("USER QUESTION:", body["messages"][-1]["content"])
        self.assertNotIn(SECRET, json.dumps(body))

    def test_reply_is_read_with_the_exact_model_and_tokens(self):
        reply = self.client.complete(self.prompt)
        self.assertEqual(reply.text, "Restrict SSH.")
        self.assertEqual((reply.provider, reply.model), ("openrouter", "test/model:free"))
        self.assertEqual(reply.served_model, GOOD["model"])
        self.assertEqual(reply.finish_reason, "stop")
        self.assertEqual(reply.usage["completion_tokens_details"]["reasoning_tokens"], 12)


class FailureTests(ServerTest):
    def test_temporary_failures_are_retried(self):
        self.server.script = [(502, {"error": {"code": 502, "message": "upstream"}}, {})]
        self.assertEqual(self.client.complete(self.prompt).text, "Restrict SSH.")
        self.assertEqual(len(self.server.requests), 2)

    def test_rate_limit_is_retried_then_reported_as_such(self):
        limited = (429, {"error": {"code": 429, "message": "Rate limit exceeded"}}, {"Retry-After": "0"})
        self.server.script = [limited] * 3
        with self.assertRaises(RateLimited):
            self.client.complete(self.prompt)
        self.assertEqual(len(self.server.requests), 3)

    def test_rejected_key_is_not_retried_and_never_echoed(self):
        self.server.script = [(401, {"error": {"code": 401, "message": f"bad key {SECRET}"}}, {})]
        with self.assertRaises(AssistantUnavailable) as caught:
            self.client.complete(self.prompt)
        self.assertEqual(len(self.server.requests), 1)
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertIn("check OPENROUTER_API_KEY", str(caught.exception))

    def test_error_inside_a_200_response(self):
        self.server.script = [(200, {"error": {"code": 429, "message": "limit"}}, {})]
        with self.assertRaises(RateLimited):
            self.client.complete(self.prompt)

    def test_reasoning_that_uses_up_the_budget_is_a_clear_failure(self):
        empty = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
        self.server.script = [(200, empty, {})]
        with self.assertRaisesRegex(AssistantUnavailable, "finish_reason: length"):
            self.client.complete(self.prompt)

    def test_unreadable_and_empty_responses(self):
        for body in (b"<html>oops</html>", {"choices": []}, {"unexpected": 1}):
            self.server.script = [(200, body, {})]
            with self.assertRaises(AssistantUnavailable):
                self.client.complete(self.prompt)

    def test_server_that_cannot_be_reached(self):
        client = OpenRouterClient(api_key=SECRET, url="http://127.0.0.1:9/chat", retry_wait_s=0)
        with self.assertRaisesRegex(AssistantUnavailable, "Could not reach"):
            client.complete(self.prompt)

    def test_redirects_are_not_followed(self):
        target = self.url + "-elsewhere"
        self.server.script = [(302, b"", {"Location": target})]  # urllib would follow a 302 by default
        with self.assertRaises(AssistantUnavailable):
            self.client.complete(self.prompt)
        self.assertEqual(len(self.server.requests), 1)


class KeyHandlingTests(unittest.TestCase):
    def test_blank_or_placeholder_keys_do_not_count(self):
        for key in ("", "   ", "<OPENROUTER_API_KEY>", "two words"):
            client = OpenRouterClient(api_key=key)
            self.assertFalse(client.configured, key)
            with self.assertRaisesRegex(AssistantUnavailable, "paste it into the .env"):
                client.complete({"system": "s", "messages": []})
        self.assertTrue(OpenRouterClient(api_key=SECRET).configured)

    def test_key_is_read_from_the_environment_and_never_shown(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": SECRET, "OPENROUTER_MODEL": "x/y"}):
            client = llm_client.get_client("openrouter")
        self.assertEqual(client.model, "x/y")
        self.assertTrue(client.configured)
        self.assertNotIn(SECRET, repr(client))
        self.assertNotIn(SECRET, json.dumps(llm_client.describe(client)))

    def test_env_file_is_parsed_and_real_variables_win(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        path = tmp / ".env"
        path.write_text("﻿# a comment\nCEP_A=one\nCEP_B = \"two\"\n\nnot a setting\nCEP_C=from-file\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"CEP_C": "from-shell"}):
            llm_client.load_env_file(path)
            self.assertEqual((os.environ["CEP_A"], os.environ["CEP_B"], os.environ["CEP_C"]), ("one", "two", "from-shell"))
        llm_client.load_env_file(tmp / "missing")  # no file is fine

    def test_example_env_file_holds_no_key(self):
        example = (llm_client.ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("OPENROUTER_API_KEY=\n", example)
        self.assertIn(llm_client.OPENROUTER_DEFAULT_MODEL, example)

    def test_git_ignores_the_env_file(self):
        self.assertIn(".env", (llm_client.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())


class AppWithOpenRouterTests(ServerTest):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test", "LOG_DIR": str(self.tmp), "LLM_PROVIDER": "none"})
        self.app.extensions["cep_llm"] = self.client
        self.web = self.app.test_client()

    def go_to_assisted_task(self):
        self.web.post("/start", data={"participant_id": "P50", "group": "3"})
        self.web.post("/consent", data={"agree": "yes"})
        self.web.post("/background", data={"year_of_study": "4", "cloud_experience": "2"})
        self.web.get("/task/P0")
        self.web.post("/api/submit/P0", json=study.task("P0")["reference_solution"])
        self.web.get("/task/A1")

    def ask(self):
        return self.web.post("/api/assistant/A1", json={"question": "Is SSH safe?",
                                                         "config": study.task("A1")["initial_config"]})

    def test_answer_reaches_the_participant_and_the_log_but_the_key_goes_nowhere(self):
        self.go_to_assisted_task()
        response = self.ask()
        self.assertEqual(response.get_json(), {"answer": "Restrict SSH."})
        answer = [e for e in self.app.extensions["cep_events"] if e["event"] == "assistant_answer"][0]
        self.assertEqual(answer["served_model"], GOOD["model"])
        self.assertEqual(answer["finish_reason"], "stop")
        self.assertEqual(answer["usage"]["total_tokens"], 30)
        logged = "".join(p.read_text(encoding="utf-8") for p in self.tmp.glob("*.jsonl"))
        self.assertNotIn(SECRET, logged)
        self.assertNotIn(SECRET, self.web.get("/task/A1").get_data(as_text=True))
        self.assertNotIn(SECRET, response.get_data(as_text=True))

    def test_model_failure_is_polite_to_the_participant_and_detailed_in_the_log(self):
        self.go_to_assisted_task()
        self.server.script = [(402, {"error": {"code": 402, "message": "Insufficient credits"}}, {})]
        response = self.ask()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("credits", response.get_data(as_text=True))
        error = [e for e in self.app.extensions["cep_events"] if e["event"] == "assistant_error"][0]
        self.assertIn("Insufficient credits", error["error"])

    def test_start_page_warns_when_the_key_is_missing(self):
        self.app.extensions["cep_llm"] = OpenRouterClient(api_key="")
        self.assertIn("has no API key", self.web.get("/").get_data(as_text=True))


class LimitedClient:
    """Answers `allowed` questions, then reports the daily limit."""
    provider, model, configured, min_interval_s = "fake", "fake-1", True, 0

    def __init__(self, allowed):
        self.allowed = allowed
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        if self.allowed <= 0:
            raise RateLimited("OpenRouter returned HTTP 429: free-models-per-day")
        self.allowed -= 1
        return llm_client.Reply("Advice about encryption.", self.provider, self.model, 1, served_model="fake-1-2026")


class OfflineRunLimits(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def run_with(self, allowed, resume=None):
        with mock.patch.object(llm_client, "get_client", return_value=LimitedClient(allowed)):
            return offline_check.run("fake", "planted", out_root=self.tmp, resume=resume)

    def read(self, folder):
        with open(folder / "answers.jsonl", encoding="utf-8") as f:
            answers = [json.loads(line) for line in f]
        with open(folder / "coding.csv", encoding="utf-8") as f:
            header = f.readline()
            rows = list(csv.reader(f))
        return answers, header, rows

    def test_daily_limit_stops_the_run_and_resume_finishes_it(self):
        folder = self.run_with(12)
        answers, _, _ = self.read(folder)
        self.assertEqual(len(answers), 12)
        self.assertEqual(answers[0]["model"]["served_model"], "fake-1-2026")
        self.assertEqual(self.run_with(100, resume=folder), folder)
        answers, header, rows = self.read(folder)
        self.assertEqual(len(answers), 30)
        self.assertEqual(len({a["case_id"] for a in answers}), 30)
        self.assertTrue(header.startswith("case_id"))
        self.assertNotIn("case_id", [r[0] for r in rows])  # the header is written only once

    def test_no_more_requests_are_sent_after_the_limit(self):
        limited = LimitedClient(5)
        with mock.patch.object(llm_client, "get_client", return_value=limited):
            offline_check.run("fake", "planted", out_root=self.tmp)
        self.assertEqual(limited.calls, 6)  # 5 answers, 1 refusal, then it stops

    def test_resume_needs_an_existing_run(self):
        with self.assertRaises(SystemExit):
            self.run_with(1, resume=self.tmp / "nothing-here")

    def test_missing_key_stops_before_any_request(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            with self.assertRaisesRegex(SystemExit, "paste it into the .env"):
                offline_check.run("openrouter", "planted", out_root=self.tmp)


if __name__ == "__main__":
    unittest.main()
