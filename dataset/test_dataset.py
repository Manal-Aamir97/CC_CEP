"""Offline tests for the dataset scripts. No network: GitHub is replaced by fakes, data by fixtures.

    python -m unittest dataset.test_dataset -v
"""
import copy
import csv
import http.client
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from dataset import baseline, check_cases, fetch_files, github, select_cases, worksheets
from validator import load_task
from validator.rules import RULES

MAPPING = json.loads(baseline.MAPPING.read_text(encoding="utf-8"))


def write_results(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(baseline.RESULT_COLUMNS)
        for repo, passed, failed in rows:
            writer.writerow([repo, len(passed), len(failed), ";".join(passed), ";".join(failed), "2022-10-13 18:00:00"])


def project(repo, passed=(), failed=()):
    return {"repo": repo, "passed": Counter(passed), "failed": Counter(failed), "date": "2022-10-13"}


class MappingTests(unittest.TestCase):
    def test_mapping_covers_every_rule_once(self):
        mapped, uncovered = set(MAPPING["rules"]), set(MAPPING["not_covered"])
        self.assertEqual(mapped | uncovered, set(RULES))
        self.assertEqual(mapped & uncovered, set())

    def test_port_80_check_is_excluded(self):
        self.assertIn("CKV_AWS_260", MAPPING["excluded_checks"])
        self.assertEqual(baseline.mapping_problems(MAPPING, {c: ["x"] for e in MAPPING["rules"].values()
                                                            for c in e["checks"]}), [])

    def test_unknown_check_is_reported(self):
        descriptions = {c: ["x"] for e in MAPPING["rules"].values() for c in e["checks"]}
        del descriptions["CKV_AWS_17"]
        self.assertEqual(baseline.mapping_problems(MAPPING, descriptions),
                         ["DB-01: CKV_AWS_17 is not in Verdet et al.'s policy file"])


class BaselineTests(unittest.TestCase):
    def test_read_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.csv"
            write_results(path, [("a/one", ["CKV_AWS_24", "CKV_AWS_24"], ["CKV_AWS_17"]),
                                 ("b/two", [], [])])
            projects, problems = baseline.read_results(path)
        self.assertEqual(problems, [])
        self.assertEqual(projects[0]["passed"]["CKV_AWS_24"], 2)
        self.assertEqual(projects[0]["failed"]["CKV_AWS_17"], 1)
        self.assertEqual(projects[1]["passed"], Counter())

    def test_counts_that_disagree_are_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(baseline.RESULT_COLUMNS)
                writer.writerow(["a/one", 5, 0, "CKV_AWS_24", "", "2022-10-13"])
            _, problems = baseline.read_results(path)
        self.assertEqual(len(problems), 1)

    def test_compute(self):
        projects = [project("a/one", passed=["CKV_AWS_24"], failed=["CKV_AWS_24", "CKV_AWS_24"]),
                    project("b/two", passed=["CKV_AWS_24"]),
                    project("c/three", passed=["CKV_AWS_19"])]
        ws01 = baseline.compute(projects, MAPPING)["WS-01"]
        self.assertEqual((ws01["resource_checks"], ws01["resource_checks_failed"]), (4, 2))
        self.assertEqual(ws01["resource_failure_rate"], 0.5)
        self.assertEqual((ws01["projects_with_resource"], ws01["projects_with_failure"]), (2, 1))

    def test_excluded_check_never_counts(self):
        projects = [project("a/one", failed=["CKV_AWS_260"])]
        rows = baseline.compute(projects, MAPPING)
        self.assertTrue(all(r["resource_checks"] == 0 for r in rows.values()))


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.projects = [project(f"org/fail{i}", failed=["CKV_AWS_17"]) for i in range(12)] + \
                        [project(f"org/clean{i}", passed=["CKV_AWS_17"]) for i in range(12)]

    def test_selection_is_reproducible_and_stratified(self):
        groups = select_cases.classify(self.projects, MAPPING)
        first = select_cases.select(groups, seed=7)
        self.assertEqual(first, select_cases.select(groups, seed=7))
        database = [r for r in first if r["resource"] == "database"]
        roles = Counter((r["status"], r["role"]) for r in database)
        self.assertEqual(roles, {("failing", "main"): 7, ("failing", "reserve"): 5,
                                 ("clean", "main"): 3, ("clean", "reserve"): 5})
        self.assertEqual(len({r["repo"] for r in first}), len(first))


class PathSafetyTests(unittest.TestCase):
    def test_unsafe_paths_are_refused(self):
        for bad in ("../x.tf", "/etc/x.tf", "C:/x.tf", "a/../../x.tf", "a\\b.tf", "a//b.tf"):
            with self.assertRaises(ValueError, msg=bad):
                github.safe_relative_path(bad)
        self.assertEqual(str(github.safe_relative_path("modules/vpc/main.tf")), "modules/vpc/main.tf")

    def test_repo_names_are_checked(self):
        for bad in ("../evil", "a/b/c", "a", "a/.."):
            with self.assertRaises(ValueError, msg=bad):
                github.check_repo_name(bad)


class RetryTests(unittest.TestCase):
    class Dropped:
        """A response whose connection drops while it is being read."""
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"partial")

    class Fine(Dropped):
        def read(self):
            return b"[]"

    def test_dropped_connection_is_retried(self):
        replies = [self.Dropped(), self.Dropped(), self.Fine()]
        with mock.patch("urllib.request.urlopen", lambda *a, **k: replies.pop(0)), \
                mock.patch.object(github, "RETRY_WAIT_S", 0):
            self.assertEqual(github.get_json("/x"), [])
        self.assertEqual(replies, [])

    def test_network_failure_gives_up_after_the_limit(self):
        with mock.patch("urllib.request.urlopen", lambda *a, **k: self.Dropped()), \
                mock.patch.object(github, "RETRY_WAIT_S", 0):
            with self.assertRaises(http.client.IncompleteRead):
                github.get_json("/x")

    def test_failed_project_is_not_saved_and_others_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            candidates = tmp / "c.csv"
            candidates.write_text("repo,resource,status,role,failed_checks\n"
                                  "a/gone,database,failing,main,\nb/fine,database,failing,main,\n", encoding="utf-8")

            def fake(repo, cutoff):
                if repo == "a/gone":
                    raise http.client.IncompleteRead(b"")
                return {"repo": repo, "status": "not_found"}

            with mock.patch.object(fetch_files, "OUT", tmp), mock.patch.object(fetch_files, "fetch_repo", fake):
                fetch_files.main(["--candidates", str(candidates)])
            saved = json.loads((tmp / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(list(saved), ["b/fine@2022-10-14"])

    def test_fill_gaps_fetches_only_the_reserves_needed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            candidates = tmp / "c.csv"
            candidates.write_text("repo,resource,status,role,failed_checks\n"
                                  "a/lost,database,failing,main,\na/kept,database,failing,main,\n"
                                  "r/gone,database,failing,reserve,\nr/good,database,failing,reserve,\n"
                                  "r/spare,database,failing,reserve,\nr/other,database,clean,reserve,\n"
                                  "b/kept,database,clean,main,\n", encoding="utf-8")
            (tmp / "MANIFEST.json").write_text(json.dumps({
                f"{repo}@2022-10-14": {"repo": repo, "status": status}
                for repo, status in [("a/lost", "not_found"), ("a/kept", "ok"), ("b/kept", "ok")]}), encoding="utf-8")
            fetched = []

            def fake(repo, cutoff):
                fetched.append(repo)
                return {"repo": repo, "status": "not_found" if repo == "r/gone" else "ok", "commit": "a" * 40, "files": []}

            with mock.patch.object(fetch_files, "OUT", tmp), mock.patch.object(fetch_files, "fetch_repo", fake):
                fetch_files.main(["--candidates", str(candidates), "--fill-gaps"])
        self.assertEqual(fetched, ["r/gone", "r/good"])


class WorksheetTests(unittest.TestCase):
    def test_blocks_are_found_with_their_line_ranges(self):
        text = ('resource "aws_instance" "web" {\n'
                '  ami = "${var.ami}"  # a comment with a {\n'
                '  tags = { Name = "}" }\n'
                '}\n\n'
                'module "db" {\n  source = "./db"\n}\n')
        self.assertEqual(worksheets.blocks(text), [("resource", "aws_instance", "web", 1, 4, 1), ("module", "db", "", 6, 8, 0)])
        self.assertEqual(worksheets.module_source(text, 6, 8), "./db")

    def test_test_and_example_folders_count_as_samples(self):
        for path in ["examples/a/main.tf", "src/test/resources/x.tf", "its/plugin/main.tf", "Demo/s3.tf"]:
            self.assertTrue(worksheets.is_sample(path), path)
        for path in ["main.tf", "modules/latest/rds.tf", "contests/s3.tf"]:
            self.assertFalse(worksheets.is_sample(path), path)

    def test_worksheet_suggests_a_real_resource_and_quotes_no_code(self):
        files = {"examples/a.tf": 'resource "aws_db_instance" "sample" {\n  password = "SECRET-VALUE"\n}\n',
                 "rds.tf": 'resource "aws_db_instance" "real" {\n  password = "SECRET-VALUE"\n}\n'
                           'resource "aws_s3_bucket" "unrelated" {\n}\n'}
        entry = {"repo": "org/proj", "commit": "a" * 40, "commit_date": "2022-09-01T10:00:00Z",
                 "files": [{"path": p, "sha256": "x"} for p in files]}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(worksheets, "FETCHED", Path(tmp)):
            for path, text in files.items():
                target = Path(tmp, "org__proj", "a" * 40, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            sheet, n_rows, n_primary, n_modules = worksheets.worksheet(
                entry, {"resource": "database", "status": "failing", "role": "main"}, {"CKV_AWS_16"})
        self.assertEqual((n_rows, n_primary, n_modules), (2, 1, 0))
        self.assertIn("`real` in `rds.tf`", sheet)
        self.assertIn("CKV_AWS_16", sheet)
        self.assertNotIn("SECRET-VALUE", sheet)
        self.assertNotIn("unrelated", sheet)

    def test_project_with_only_samples_has_no_suggestion(self):
        entry = {"repo": "org/proj", "commit": "a" * 40, "commit_date": "2022-09-01T10:00:00Z",
                 "files": [{"path": "test/a.tf", "sha256": "x"}]}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(worksheets, "FETCHED", Path(tmp)):
            target = Path(tmp, "org__proj", "a" * 40, "test", "a.tf")
            target.parent.mkdir(parents=True)
            target.write_text('resource "aws_s3_bucket" "b" {\n}\n', encoding="utf-8")
            sheet, n_rows, n_primary, _ = worksheets.worksheet(
                entry, {"resource": "storage_bucket", "status": "clean", "role": "main"}, set())
        self.assertEqual((n_rows, n_primary), (1, 0))
        self.assertIn("choose a reserve", sheet)


class TokenTests(unittest.TestCase):
    def test_token_goes_only_to_the_api(self):
        seen = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"[]"

        def fake_urlopen(request, timeout):
            seen.append((request.full_url, request.get_header("Authorization")))
            return Response()

        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}), \
                mock.patch("urllib.request.urlopen", fake_urlopen):
            github.get_json("/repos/a/b/commits", per_page=1)
            github.get_raw("a/b", "abc123", "main.tf")
        self.assertTrue(seen[0][0].startswith(github.API) and seen[0][1] == "Bearer test-token")
        self.assertTrue(seen[1][0].startswith(github.RAW) and seen[1][1] is None)


class FetchTests(unittest.TestCase):
    def fake_github(self, commits=True, missing_repo=False):
        calls = []

        def get_json(path, **query):
            calls.append((path, query))
            if missing_repo:
                raise github.NotFound(path)
            if path.endswith("/commits"):
                return [{"sha": "a" * 40, "commit": {"committer": {"date": "2022-09-01T10:00:00Z"}}}] if commits else []
            return {"truncated": False, "tree": [
                {"type": "blob", "path": "main.tf", "size": 10},
                {"type": "blob", "path": "modules/db/rds.tf", "size": 10},
                {"type": "blob", "path": "README.md", "size": 10},
                {"type": "blob", "path": "../escape.tf", "size": 10},
                {"type": "tree", "path": "modules"}]}

        return calls, get_json, lambda repo, ref, path: f"# {path}".encode()

    def test_fetch_pins_the_commit_and_saves_only_terraform(self):
        calls, get_json, get_raw = self.fake_github()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(github, "get_json", get_json), mock.patch.object(github, "get_raw", get_raw):
            record = fetch_files.fetch_repo("org/proj", "2022-10-14", Path(tmp))
            saved = sorted(str(p.relative_to(tmp)).replace("\\", "/") for p in Path(tmp).rglob("*.tf"))
        self.assertEqual(calls[0], ("/repos/org/proj/commits", {"until": "2022-10-14T00:00:00Z", "per_page": 1}))
        self.assertEqual(record["status"], "ok")
        self.assertEqual(saved, [f"org__proj/{'a' * 40}/main.tf", f"org__proj/{'a' * 40}/modules/db/rds.tf"])
        self.assertIn({"path": "../escape.tf", "status": "unsafe_path_skipped"}, record["files"])

    def test_missing_repo_and_no_old_commit(self):
        _, get_json, get_raw = self.fake_github(missing_repo=True)
        with mock.patch.object(github, "get_json", get_json):
            self.assertEqual(fetch_files.fetch_repo("org/gone", "2022-10-14")["status"], "not_found")
        _, get_json, get_raw = self.fake_github(commits=False)
        with mock.patch.object(github, "get_json", get_json):
            self.assertEqual(fetch_files.fetch_repo("org/new", "2022-10-14")["status"], "no_commit_before_cutoff")


class CaseTests(unittest.TestCase):
    def make_case(self):
        config = copy.deepcopy(load_task("A1")["reference_solution"])
        config["disk_encryption"] = False
        return {"task_id": "C01", "set": "offline", "scored": True, "resource": "web_server",
                "participant": {"title": "t", "scenario": "s", "requirements": [{"id": "R1", "text": "x"},
                                                                                 {"id": "R2", "text": "y"}]},
                "scoring": {"rules": ["WS-01", "WS-06"], "params": {},
                            "requirement_map": {"R1": ["WS-01"], "R2": ["WS-06"]}},
                "initial_config": config,
                "provenance": {"repo": "org/proj", "commit": "a" * 40, "commit_date": "2022-09-01",
                               "path": "main.tf", "lines": "1-20", "converted_by": "test", "conversion_notes": ""}}

    def test_valid_case_scores_like_a_task(self):
        case = self.make_case()
        fetched = {("org/proj", "a" * 40): {"main.tf"}}
        self.assertEqual(check_cases.case_problems(case, fetched), [])
        from validator import validate
        self.assertEqual(validate(case["initial_config"], case).failed_rules, ["WS-06"])

    def test_case_must_trace_to_a_fetched_file(self):
        case = self.make_case()
        case["provenance"]["path"] = "other.tf"
        problems = check_cases.case_problems(case, {("org/proj", "a" * 40): {"main.tf"}})
        self.assertTrue(any("not among the fetched files" in p for p in problems))

    def test_case_rules_must_fit_the_resource(self):
        case = self.make_case()
        case["scoring"]["rules"] = ["WS-01", "DB-01"]
        case["scoring"]["requirement_map"] = {"R1": ["WS-01"], "R2": ["DB-01"]}
        problems = check_cases.case_problems(case, None)
        self.assertTrue(any("DB-01 does not apply" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
