"""Tests for the web app (build plan Steps 4 to 6). Run from the repository root:

    python -m unittest app.test_app -v
"""
import copy
import ipaddress
import json
import re
import tempfile
import unittest
from html import unescape as html_unescape
from pathlib import Path

from app import study
from app.app import MAX_QUESTIONS_PER_TASK, create_app
from app.events import read_events
from llm.prompt import FORM_GUIDES
from validator.schema import load_schema

RULE_ID = re.compile(r"\b(WS|ST|DB)-\d{2}\b")
RESEARCHER_WORDS = ("reference_solution", "requirement_map", "scoring", "traps", "decision_points",
                    "severity", "weighted")
POST_ANSWERS = {"helpfulness": "4", "trust": "3", "confidence": "5", "comments": "fine"}
# Words a person with no cloud knowledge would not know. They must not appear on participants' screens.
JARGON = re.compile(r"\b(SSH|RDP|TCP|CIDR|ACLs?|KMS|SSE|vCPU|GiB|ingress|inbound|bucket|buckets|instance|port|ports|"
                    r"security group|IP address|console|configuration|configurations|resource|resources|"
                    r"provision|deploy|root volume|0\.0\.0\.0)\b", re.I)


def jargon_in(html):
    """Jargon in what a person can read: scripts (the form's hidden data), tags and the researcher's
    preview panel are left out."""
    text = re.sub(r"<script\b.*?</script>", " ", html, flags=re.S)
    text = re.sub(r'<section class="panel researcher".*?</section>', " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return sorted({m.group(0) for m in JARGON.finditer(html_unescape(text))})


class AppTest(unittest.TestCase):
    preview = False
    provider = "stub"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_dir = Path(self.tmp.name)
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test", "RESEARCHER_PREVIEW": self.preview,
                               "LOG_DIR": str(self.log_dir), "LLM_PROVIDER": self.provider})
        self.client = self.app.test_client()
        self.events = self.app.extensions["cep_events"]

    # -------------------------------------------------------------- helpers

    def start(self, participant_id="P01", group="1"):
        return self.client.post("/start", data={"participant_id": participant_id, "group": group})

    def submit(self, task_id, config):
        return self.client.post(f"/api/submit/{task_id}", json=config)

    def do_step(self, step):
        kind = step["kind"]
        if kind == "consent":
            self.assertEqual(self.client.post("/consent", data={"agree": "yes"}).status_code, 302)
        elif kind == "background":
            r = self.client.post("/background", data={"year_of_study": "4", "cloud_experience": "2"})
            self.assertEqual(r.status_code, 302)
        elif kind == "task":
            self.client.get(f"/task/{step['task_id']}")
            r = self.submit(step["task_id"], study.task(step["task_id"])["reference_solution"])
            self.assertEqual(r.status_code, 200, r.get_json())
        elif kind == "break":
            self.client.post("/break")
        elif kind == "post":
            self.assertEqual(self.client.post("/post", data=POST_ANSWERS).status_code, 302)

    def go_to(self, task_id, group="1", participant_id="P01"):
        """Start a session and complete every step until task_id is open (its page visited)."""
        self.start(participant_id=participant_id, group=group)
        for step in study.schedule(group):
            if step["kind"] == "task" and step["task_id"] == task_id:
                self.client.get(f"/task/{task_id}")
                return step
            self.do_step(step)
        raise AssertionError(f"{task_id} is not in group {group}'s schedule")

    def kinds(self):
        return [e["event"] for e in self.events]

    def session_data(self):
        cookie = self.client.get_cookie("session")
        return self.app.session_interface.get_signing_serializer(self.app).loads(cookie.value)


class FlowTests(AppTest):
    def test_start_page_names_the_model_state(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("Start a participant session", html)
        self.assertIn("Never use it with participants", html)  # stub warning for the researcher

    def test_bad_participant_id_or_group_is_refused(self):
        self.assertEqual(self.start(participant_id="X1").status_code, 400)
        self.assertEqual(self.start(group="9").status_code, 400)
        self.assertEqual(self.events, [])

    def test_session_opens_with_consent_then_background_then_practice(self):
        self.start()
        self.assertEqual(self.client.get("/next").headers["Location"], "/consent")
        self.assertEqual(self.client.post("/consent", data={}).status_code, 400)  # box not ticked
        self.client.post("/consent", data={"agree": "yes"})
        self.assertEqual(self.client.get("/next").headers["Location"], "/background")
        self.assertEqual(self.client.post("/background", data={"year_of_study": "4"}).status_code, 400)
        self.client.post("/background", data={"year_of_study": "4", "cloud_experience": "3"})
        self.assertEqual(self.client.get("/next").headers["Location"], "/task/P0")
        background = [e for e in self.events if e["event"] == "background"][0]
        self.assertEqual((background["year_of_study"], background["cloud_experience"]), ("4", 3))

    def test_schedule_follows_sets_json(self):
        sets = study.sets()
        for group, parts in sets["counterbalancing_groups"].items():
            expected = [("consent", None, None), ("background", None, None), ("task", "P0", "practice")]
            for n, part in enumerate(parts, 1):
                expected += [("task", t, part["condition"]) for t in sets["sets"][part["set"]]]
                if n == 1:
                    expected.append(("break", None, None))
            expected += [("post", None, None), ("done", None, None)]
            steps = study.schedule(group)
            self.assertEqual([(s["kind"], s.get("task_id"), s.get("condition")) for s in steps], expected, group)

    def test_only_the_open_task_can_be_opened_or_submitted(self):
        self.go_to("P0")
        self.assertEqual(self.client.get("/task/A1").headers["Location"], "/next")
        self.assertEqual(self.submit("A1", study.task("A1")["reference_solution"]).status_code, 409)

    def test_submit_needs_json(self):
        self.go_to("P0")
        self.assertEqual(self.client.post("/api/submit/P0", data="x").status_code, 415)

    def test_full_session_for_every_group(self):
        for group in study.groups():
            with self.subTest(group=group):
                self.setUp()
                self.start(participant_id="P07", group=group)
                for step in study.schedule(group):
                    if step["kind"] == "break":
                        self.assertIn(b"Part 1 complete", self.client.get("/next").data)
                    if step["kind"] == "done":
                        self.assertIn(b"All tasks complete", self.client.get("/next").data)
                    self.do_step(step)
                submits = [e for e in self.events if e["event"] == "task_submit"]
                expected = [(s["task_id"], s["condition"]) for s in study.schedule(group) if s["kind"] == "task"]
                self.assertEqual([(e["task_id"], e["condition"]) for e in submits], expected)
                self.assertTrue(all(e["result"]["error_count"] == 0 for e in submits if e["scored"]))
                self.assertIsNone(submits[0]["result"])  # the practice task is never scored
                self.assertEqual(self.kinds().count("session_end"), 1)
                self.assertEqual(self.kinds().count("post_questionnaire"), 1)
                files = sorted(p.name for p in self.log_dir.glob("P07_*.jsonl"))
                self.assertEqual(files, [f"P07_session{n}.jsonl" for n in range(4)])

    def test_a_used_participant_id_cannot_start_again(self):
        self.go_to("P0")
        fresh = self.app.test_client()
        r = fresh.post("/start", data={"participant_id": "P01", "group": "2"})
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"already exist", r.data)


class SubmitTests(AppTest):
    def test_scored_result_never_reaches_the_participant(self):
        self.go_to("A1")
        response = self.submit("A1", study.task("A1")["initial_config"])  # two traps left in
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.get_json()), {"ok", "next"})
        self.assertIsNone(RULE_ID.search(response.get_data(as_text=True)))
        event = [e for e in self.events if e["event"] == "task_submit"][-1]
        self.assertEqual(event["result"]["failed_rules"], ["WS-01", "WS-06"])
        self.assertEqual(event["condition"], "unassisted")  # group 1, part 1
        cookie = self.session_data()
        self.assertLessEqual(set(cookie), {"participant_id", "group", "step", "started", "ended"})
        self.assertIsNone(RULE_ID.search(json.dumps(cookie)))

    def test_duration_is_measured_from_the_first_page_view(self):
        self.go_to("A1")
        self.submit("A1", study.task("A1")["reference_solution"])
        event = [e for e in self.events if e["event"] == "task_submit"][-1]
        self.assertIsInstance(event["duration_s"], float)
        self.assertGreaterEqual(event["duration_s"], 0)

    def test_incomplete_form_is_sent_back_and_logged_without_scoring(self):
        self.go_to("A2")
        response = self.submit("A2", study.task("A2")["initial_config"])  # encryption and versioning unchosen
        self.assertEqual(response.status_code, 400)
        problems = " ".join(response.get_json()["problems"])
        self.assertIn("encryption", problems)
        self.assertIn("versioning", problems)
        self.assertEqual(self.kinds()[-1], "submit_rejected")
        self.assertEqual(self.client.get("/next").headers["Location"], "/task/A2")

    def test_invalid_config_is_refused(self):
        self.go_to("A1")
        config = copy.deepcopy(study.task("A1")["reference_solution"])
        config["ingress"][0]["source"] = "999.0.0.1/32"
        response = self.submit("A1", config)
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a valid CIDR", response.get_json()["problems"][0])

    def test_wrong_resource_is_refused(self):
        self.go_to("A1")
        self.assertEqual(self.submit("A1", study.task("A2")["reference_solution"]).status_code, 400)


class LoggingTests(AppTest):
    def test_every_line_is_a_complete_event_in_the_right_file(self):
        self.start(participant_id="P09", group="1")
        for step in study.schedule("1"):
            self.do_step(step)
        self.client.get("/next")
        events = read_events(self.log_dir)
        self.assertEqual(len(events), len(self.events))
        for e in events:
            self.assertTrue({"event", "at", "participant_id", "group", "part"} <= set(e), e)
        by_kind = {e["event"]: e["part"] for e in events}
        self.assertEqual(by_kind["consent"], 0)
        self.assertEqual(by_kind["post_questionnaire"], 3)
        a1 = [e for e in events if e.get("task_id") == "A1" and e["event"] == "task_submit"][0]
        self.assertEqual(a1["part"], 1)  # group 1 does Set A in part 1
        session_start = [e for e in events if e["event"] == "session_start"][0]
        self.assertEqual(session_start["llm"]["temperature"], 0)

    def test_task_start_once_then_reloads(self):
        self.go_to("A1")
        self.client.get("/task/A1")
        self.assertEqual(self.kinds().count("task_start") - 1, 1)  # P0 plus A1
        self.assertEqual(self.kinds()[-1], "task_reload")
        start = [e for e in self.events if e["event"] == "task_start" and e["task_id"] == "A1"][0]
        self.assertEqual(start["initial_config"], study.task("A1")["initial_config"])

    def test_field_changes_are_recorded(self):
        self.go_to("A1")
        config = study.task("A1")["initial_config"]
        batch = {"events": [
            {"type": "field_change", "path": "disk_encryption", "old": False, "new": True,
             "at": "2026-10-28T10:00:00Z", "config": config},
            {"type": "draft_restored", "at": "2026-10-28T10:00:01Z", "config": config},
            {"type": "unknown", "config": config},
            "not an object",
        ]}
        r = self.client.post("/api/events/A1", json=batch)
        self.assertEqual(r.get_json(), {"ok": True, "recorded": 2})
        change = [e for e in self.events if e["event"] == "field_change"][0]
        self.assertEqual((change["path"], change["old"], change["new"]), ("disk_encryption", False, True))

    def test_field_changes_need_the_open_task_and_a_sane_batch(self):
        self.go_to("A1")
        self.assertEqual(self.client.post("/api/events/A2", json={"events": []}).status_code, 409)
        self.assertEqual(self.client.post("/api/events/A1", json={"events": "x"}).status_code, 400)
        too_many = {"events": [{"type": "field_change", "path": "x", "config": {}}] * 201}
        self.assertEqual(self.client.post("/api/events/A1", json=too_many).status_code, 400)


class AssistantTests(AppTest):
    def ask(self, task_id, question, config=None):
        config = config if config is not None else study.task(task_id)["initial_config"]
        return self.client.post(f"/api/assistant/{task_id}", json={"question": question, "config": config})

    def test_panel_and_endpoint_only_in_the_assisted_part(self):
        self.go_to("A1", group="1")  # part 1 of group 1 is unassisted
        self.assertNotIn("assistant-panel", self.client.get("/task/A1").get_data(as_text=True))
        self.assertEqual(self.ask("A1", "Help?").status_code, 403)

    def test_question_answer_and_prompt_are_logged(self):
        self.go_to("A1", group="3")  # part 1 of group 3 is assisted
        html = self.client.get("/task/A1").get_data(as_text=True)
        self.assertIn("assistant-panel", html)
        r = self.ask("A1", "Is my SSH rule safe?")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Is my SSH rule safe?", r.get_json()["answer"])
        question = [e for e in self.events if e["event"] == "assistant_question"][-1]
        answer = [e for e in self.events if e["event"] == "assistant_answer"][-1]
        self.assertEqual(question["question"], "Is my SSH rule safe?")
        self.assertIn("CURRENT CONFIGURATION:", question["prompt"]["messages"][-1]["content"])
        self.assertIsNone(RULE_ID.search(json.dumps(question["prompt"])))
        self.assertEqual((answer["provider"], answer["model"]), ("stub", "stub-v1"))

    def test_follow_up_questions_carry_the_conversation(self):
        self.go_to("A1", group="3")
        self.ask("A1", "first question")
        self.ask("A1", "second question")
        prompt = [e for e in self.events if e["event"] == "assistant_question"][-1]["prompt"]
        self.assertEqual(prompt["messages"][0]["content"], "first question")
        self.assertIn("second question", prompt["messages"][-1]["content"])
        html = self.client.get("/task/A1").get_data(as_text=True)  # history survives a reload
        self.assertIn("first question", html)

    def test_bad_questions_are_refused(self):
        self.go_to("A1", group="3")
        self.assertEqual(self.ask("A1", "   ").status_code, 400)
        self.assertEqual(self.ask("A1", "x" * 1001).status_code, 400)
        self.assertEqual(self.ask("A1", "ok?", config=study.task("A2")["initial_config"]).status_code, 400)
        self.assertEqual(self.client.post("/api/assistant/A1", data="x").status_code, 415)
        self.assertNotIn("assistant_question", self.kinds())

    def test_the_model_gets_the_exact_wording_and_the_page_shows_the_plain_wording(self):
        self.go_to("A1", group="3")
        html = html_unescape(self.client.get("/task/A1").get_data(as_text=True))
        self.ask("A1", "Is this right?")
        prompt = [e for e in self.events if e["event"] == "assistant_question"][-1]["prompt"]
        text = prompt["messages"][-1]["content"]
        task = study.task("A1")
        for requirement in task["participant"]["requirements"]:
            self.assertIn(requirement["text"], text)
            self.assertNotIn(requirement["text"], html)
        self.assertIn(json.dumps(task["initial_config"], indent=2, sort_keys=True), text)
        self.assertIn(task["display"]["requirements"][1]["text"], html)
        self.assertNotIn(task["display"]["requirements"][1]["text"], text)

    def test_question_limit_per_task(self):
        self.go_to("A1", group="3")
        for i in range(MAX_QUESTIONS_PER_TASK):
            self.assertEqual(self.ask("A1", f"q{i}").status_code, 200)
        self.assertEqual(self.ask("A1", "one more").status_code, 429)


class UnavailableAssistantTests(AppTest):
    provider = "none"

    def test_no_model_means_a_polite_error_and_a_logged_failure(self):
        self.go_to("A1", group="3")
        r = self.client.post("/api/assistant/A1", json={"question": "Help?",
                                                         "config": study.task("A1")["initial_config"]})
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("detail", r.get_json())
        self.assertEqual(self.kinds()[-2:], ["assistant_question", "assistant_error"])


class QuestionnaireTests(AppTest):
    def test_post_questionnaire_needs_all_three_answers(self):
        self.start(group="1")
        for step in study.schedule("1"):
            if step["kind"] == "post":
                break
            self.do_step(step)
        self.assertEqual(self.client.post("/post", data={"helpfulness": "4"}).status_code, 400)
        self.client.post("/post", data={**POST_ANSWERS, "comments": "y" * 5000})
        answers = [e for e in self.events if e["event"] == "post_questionnaire"][0]
        self.assertEqual((answers["helpfulness"], answers["trust"], answers["confidence"]), (4, 3, 5))
        self.assertEqual(len(answers["comments"]), 1000)


class PageContentTests(AppTest):
    preview = True

    def test_pages_carry_only_public_task_data(self):
        for task_id in study.all_task_ids():
            full = study.task(task_id)
            html = self.client.get(f"/preview/{task_id}?assisted=1").get_data(as_text=True)
            participant_text = " ".join([full["display"]["scenario"]] +
                                        [r["text"] for r in full["display"]["requirements"]])
            with self.subTest(task=task_id):
                self.assertIn(full["display"]["title"].replace("&", "&amp;"), html)
                self.assertIsNone(RULE_ID.search(html))
                for word in RESEARCHER_WORDS:
                    self.assertNotIn(word, html)
                for trap in full["design"]["traps"]:
                    self.assertNotIn(trap["basis"][:40], html)
                for value in full["scoring"]["params"].values():
                    if isinstance(value, str) and value not in participant_text:
                        self.assertNotIn(value, html)

    def test_pages_use_no_cloud_jargon(self):
        for task_id in study.all_task_ids():
            with self.subTest(task=task_id):
                self.assertEqual(jargon_in(self.client.get(f"/preview/{task_id}?assisted=1").get_data(as_text=True)), [])
        self.start(participant_id="P03", group="1")
        for step in study.schedule("1"):
            with self.subTest(step=step["kind"]):
                self.assertEqual(jargon_in(self.client.get("/next", follow_redirects=True).get_data(as_text=True)), [])
            self.do_step(step)

    def test_rule_labels_in_the_browser_data_use_no_jargon(self):
        self.assertEqual(jargon_in(" ".join(t["label"] for t in study.INGRESS_TYPES)), [])
        script = (Path(study.ROOT) / "app" / "static" / "form.js").read_text(encoding="utf-8")
        shown = re.findall(r'(?:textContent = |text: |placeholder = |confirm\(|setAttribute\("aria-label", )("[^"]*")', script)
        self.assertGreater(len(shown), 5)
        self.assertEqual(jargon_in(" ".join(shown)), [])

    def test_the_form_words_the_model_is_told_are_on_the_form(self):
        """Every quoted label in FORM_GUIDES must be what the form really shows."""
        script = (Path(study.ROOT) / "app" / "static" / "form.js").read_text(encoding="utf-8")
        for resource, task_id in (("web_server", "A1"), ("storage_bucket", "A2"), ("database", "A3")):
            html = html_unescape(self.client.get(f"/preview/{task_id}").get_data(as_text=True))
            html = re.sub(r"</?(em|strong)>", "", html)
            words = html + script.replace('\\"', '"') + " ".join(t["label"] for t in study.INGRESS_TYPES)
            for label in re.findall(r'"([^"]+)"', FORM_GUIDES[resource]):
                if label not in ("source", "0.0.0.0/0"):
                    with self.subTest(resource=resource, label=label):
                        self.assertIn(label, words)

    def test_participant_page_has_no_preview_parts(self):
        app = create_app({"TESTING": True, "SECRET_KEY": "test", "LOG_DIR": str(self.log_dir)})
        client = app.test_client()
        client.post("/start", data={"participant_id": "P02", "group": "1"})
        client.post("/consent", data={"agree": "yes"})
        client.post("/background", data={"year_of_study": "1", "cloud_experience": "1"})
        html = client.get("/task/P0").get_data(as_text=True)
        self.assertNotIn("json-preview", html)
        self.assertNotIn("Researcher preview", html)
        self.assertIn('data-draft-key="cep-draft:P02:P0"', html)
        self.assertIn('data-events-url="/api/events/P0"', html)

    def test_security_headers(self):
        response = self.client.get("/preview/A1")
        self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_static_files(self):
        for name in ("form.js", "events.js", "assistant.js", "markdown.js", "console.css"):
            response = self.client.get(f"/static/{name}")
            self.assertEqual(response.status_code, 200, name)
            response.close()


class PreviewTests(AppTest):
    def test_preview_is_off_by_default(self):
        self.assertEqual(self.client.get("/preview").status_code, 404)
        self.assertEqual(self.client.get("/preview/A1").status_code, 404)
        self.assertEqual(self.client.post("/api/preview/A1", json={}).status_code, 404)
        self.assertEqual(self.client.post("/api/preview/assistant/A1", json={}).status_code, 404)

    def test_preview_shows_the_verdict_and_records_nothing(self):
        app = create_app({"TESTING": True, "SECRET_KEY": "test", "RESEARCHER_PREVIEW": True,
                          "LOG_DIR": str(self.log_dir), "LLM_PROVIDER": "stub"})
        client = app.test_client()
        result = client.post("/api/preview/A1", json=study.task("A1")["initial_config"]).get_json()["result"]
        self.assertEqual(result["failed_rules"], ["WS-01", "WS-06"])
        self.assertIsNone(client.post("/api/preview/P0", json=study.task("P0")["initial_config"]).get_json()["result"])
        answer = client.post("/api/preview/assistant/A1", json={"question": "Hi?",
                                                                 "config": study.task("A1")["initial_config"]})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(app.extensions["cep_events"], [])
        self.assertEqual(list(self.log_dir.glob("*.jsonl")), [])


class FormOptionTests(unittest.TestCase):
    def test_option_values_match_the_schema(self):
        defs = load_schema()["$defs"]
        where = {"instance_type": "web_server", "os": "web_server", "region": "storage_bucket",
                 "engine": "database", "instance_class": "database",
                 "encryption": "storage_bucket", "versioning": "storage_bucket"}
        for field, resource in where.items():
            enum = [v for v in defs[resource]["properties"][field]["enum"] if v is not None]
            values = [value for value, _ in study.FORM_OPTIONS[field]]
            self.assertEqual(sorted(values), sorted(enum), field)  # display order may differ
            self.assertEqual(len(values), len(set(values)), field)
        days = defs["database"]["properties"]["backup_retention_days"]
        self.assertEqual([int(v) for v, _ in study.FORM_OPTIONS["backup_retention_days"]],
                         list(range(days["minimum"], days["maximum"] + 1)))

    def test_rule_type_presets(self):
        ports = {t["key"]: t["port"] for t in study.INGRESS_TYPES}
        self.assertEqual(ports, {"ssh": 22, "http": 80, "https": 443, "rdp": 3389, "custom": None})

    def test_answers_are_never_inserted_as_html(self):
        for name in ("assistant.js", "markdown.js"):
            script = (Path(study.ROOT) / "app" / "static" / name).read_text(encoding="utf-8")
            for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "createElement(\"a\")"):
                self.assertNotIn(risky, script, name)

    def test_placeholder_example_is_no_task_address(self):
        """The source placeholder must not give any task's answer away (or favour one set)."""
        script = (Path(study.ROOT) / "app" / "static" / "form.js").read_text(encoding="utf-8")
        example = ipaddress.ip_network(re.search(r'EXAMPLE_SOURCE = "([^"]+)"', script).group(1))
        for task_id in study.all_task_ids():
            full = study.task(task_id)
            self.assertNotIn(str(example.network_address), str(full["participant"]), task_id)
            for value in full["scoring"]["params"].values():
                if isinstance(value, str) and "/" in value:
                    self.assertFalse(ipaddress.ip_network(value).overlaps(example), task_id)


if __name__ == "__main__":
    unittest.main()
