"""Unit tests for the schema checker and the rule-based validator.

Run from the repository root:

    python -m unittest validator.test_rules -v
"""
import ast
import copy
import ipaddress
import json
import unittest
from pathlib import Path

from validator import ConfigError, config_problems, load_task, submission_problems, validate
from validator import schema as schema_module
from validator.rules import RULES
from validator.validator import ROOT, catalogue_rules, load_catalogue

try:
    import jsonschema
except ImportError:
    jsonschema = None

SETS = json.loads((ROOT / "tasks" / "sets.json").read_text(encoding="utf-8-sig"))
SCORED = SETS["sets"]["A"] + SETS["sets"]["B"]
ALL_TASKS = SCORED + [SETS["practice"]]
TASKS = {tid: load_task(tid) for tid in ALL_TASKS}


def failing(config, task):
    return set(validate(config, task).failed_rules)


def secure(task):
    return copy.deepcopy(task["reference_solution"])


def tasks_for(resource):
    return [TASKS[tid] for tid in SCORED if TASKS[tid]["resource"] == resource]


# ---------------------------------------------------------------- mutation helpers
# Each takes (cfg, params) and changes cfg in place, starting from a fully secure answer.

def _port(cfg, port):
    return cfg["port"] if port == "db" else port


def set_source(port, source):
    def mutate(cfg, params):
        for rule in cfg["ingress"]:
            if rule["port"] == _port(cfg, port):
                rule["source"] = source(params) if callable(source) else source
    return mutate


def add_rule(port, source):
    def mutate(cfg, params):
        value = source(params) if callable(source) else source
        cfg["ingress"].append({"port": _port(cfg, port), "protocol": "tcp", "source": value})
    return mutate


def drop_port(port):
    def mutate(cfg, params):
        cfg["ingress"] = [r for r in cfg["ingress"] if r["port"] != _port(cfg, port)]
    return mutate


def set_field(path, value):
    def mutate(cfg, params):
        *parents, last = path.split(".")
        node = cfg
        for key in parents:
            node = node[key]
        node[last] = value(cfg, params) if callable(value) else value
    return mutate


def widen(param, prefix):
    return lambda params: str(ipaddress.ip_network(params[param]).supernet(new_prefix=prefix))


def typo(param):
    """Same network, last octet off by 27: the kind of slip a participant makes."""
    return lambda params: f"{ipaddress.ip_network(params[param]).network_address + 27}/32"


def host_bits(param, prefix):
    """The right address typed with the wrong prefix, e.g. 203.0.113.25/24."""
    return lambda params: f"{ipaddress.ip_network(params[param]).network_address}/{prefix}"


# (resource, rule that must fail, description, mutation). Each case must fail that rule ONLY.
NEGATIVE_CASES = [
    ("web_server", "WS-01", "SSH source changed to 0.0.0.0/0", set_source(22, "0.0.0.0/0")),
    ("web_server", "WS-01", "extra SSH rule from ::/0", add_rule(22, "::/0")),
    ("web_server", "WS-02", "SSH open to the admin's whole /24", set_source(22, widen("admin_cidr", 24))),
    ("web_server", "WS-02", "admin address mistyped", set_source(22, typo("admin_cidr"))),
    ("web_server", "WS-02", "admin address typed with /24", set_source(22, host_bits("admin_cidr", 24))),
    ("web_server", "WS-02", "SSH rule deleted (admin locked out)", drop_port(22)),
    ("web_server", "WS-02", "extra SSH source 10.0.0.0/8", add_rule(22, "10.0.0.0/8")),
    ("web_server", "WS-03", "RDP rule from 0.0.0.0/0 added", add_rule(3389, "0.0.0.0/0")),
    ("web_server", "WS-03", "RDP rule from ::/0 added", add_rule(3389, "::/0")),
    ("web_server", "WS-04", "HTTP rule deleted", drop_port(80)),
    ("web_server", "WS-04", "HTTP limited to 10.0.0.0/8", set_source(80, "10.0.0.0/8")),
    ("web_server", "WS-05", "HTTPS rule deleted", drop_port(443)),
    ("web_server", "WS-05", "HTTPS open over IPv6 only", set_source(443, "::/0")),
    ("web_server", "WS-06", "disk encryption off", set_field("disk_encryption", False)),
    ("storage_bucket", "ST-01", "block_public_acls off", set_field("block_public_access.block_public_acls", False)),
    ("storage_bucket", "ST-01", "ignore_public_acls off", set_field("block_public_access.ignore_public_acls", False)),
    ("storage_bucket", "ST-01", "block_public_policy off", set_field("block_public_access.block_public_policy", False)),
    ("storage_bucket", "ST-01", "restrict_public_buckets off", set_field("block_public_access.restrict_public_buckets", False)),
    ("storage_bucket", "ST-02", "encryption none", set_field("encryption", "none")),
    ("storage_bucket", "ST-02", "encryption never chosen", set_field("encryption", None)),
    ("storage_bucket", "ST-03", "versioning disabled", set_field("versioning", "disabled")),
    ("storage_bucket", "ST-03", "versioning never chosen", set_field("versioning", None)),
    ("storage_bucket", "ST-04", "access logging off", set_field("access_logging.enabled", False)),
    ("storage_bucket", "ST-04", "logs written into the bucket itself",
     set_field("access_logging.target_bucket", lambda cfg, params: cfg["bucket_name"])),
    ("storage_bucket", "ST-04", "logs sent to another bucket", set_field("access_logging.target_bucket", "some-other-logs")),
    ("storage_bucket", "ST-04", "logging on with no target", set_field("access_logging.target_bucket", "")),
    ("database", "DB-01", "publicly accessible", set_field("publicly_accessible", True)),
    ("database", "DB-02", "DB port source changed to 0.0.0.0/0", set_source("db", "0.0.0.0/0")),
    ("database", "DB-02", "extra DB rule from ::/0", add_rule("db", "::/0")),
    ("database", "DB-03", "DB port open to the whole /16", set_source("db", widen("app_cidr", 16))),
    ("database", "DB-03", "DB source left empty", set_source("db", "")),
    ("database", "DB-03", "app server address mistyped", set_source("db", typo("app_cidr"))),
    ("database", "DB-03", "extra DB source 10.0.9.9/32", add_rule("db", "10.0.9.9/32")),
    ("database", "DB-04", "storage not encrypted", set_field("storage_encrypted", False)),
    ("database", "DB-05", "backups off (0 days)", set_field("backup_retention_days", 0)),
    ("database", "DB-05", "backups kept 6 days", set_field("backup_retention_days", 6)),
]

# Changes that are still fully secure: every rule must pass.
POSITIVE_CASES = [
    ("web_server", "admin SSH rule listed twice", add_rule(22, lambda p: p["admin_cidr"])),
    ("web_server", "HTTP and HTTPS also open over IPv6", lambda c, p: (add_rule(80, "::/0")(c, p), add_rule(443, "::/0")(c, p))),
    ("web_server", "unscored extra rule (8080 from 10.0.0.0/8)", add_rule(8080, "10.0.0.0/8")),
    ("storage_bucket", "SSE-KMS instead of SSE-S3", set_field("encryption", "sse-kms")),
    ("database", "backups kept exactly 7 days", set_field("backup_retention_days", 7)),
    ("database", "backups kept 35 days", set_field("backup_retention_days", 35)),
]

# Valid-looking edits the schema or semantic check must reject: (task, description, mutation, expected text).
INVALID_CASES = [
    ("A1", "unknown field", set_field("tags", {"team": "web"}), "unexpected field 'tags'"),
    ("A1", "port as a string", lambda c, p: c["ingress"][0].update(port="22"), "expected integer"),
    ("A1", "port above 65535", lambda c, p: c["ingress"][0].update(port=70000), "greater than the maximum"),
    ("A1", "address without a prefix", set_source(22, "203.0.113.25"), "not in the expected format"),
    ("A1", "address that does not parse", set_source(22, "999.0.0.1/32"), "not a valid CIDR"),
    ("A1", "UDP rule", lambda c, p: c["ingress"][0].update(protocol="udp"), 'must be "tcp"'),
    ("A1", "missing disk_encryption", lambda c, p: c.pop("disk_encryption"), "missing required field 'disk_encryption'"),
    ("A1", "unknown instance type", set_field("instance_type", "m5.24xlarge"), "is not one of"),
    ("A1", "wrong resource name", set_field("resource", "vm"), "is not one of"),
    ("A2", "unknown encryption option", set_field("encryption", "aes"), "is not one of"),
    ("A2", "missing Block Public Access switch",
     lambda c, p: c["block_public_access"].pop("ignore_public_acls"), "missing required field 'ignore_public_acls'"),
    ("A2", "versioning as a boolean", set_field("versioning", True), "is not one of"),
    ("A3", "retention as a boolean", set_field("backup_retention_days", True), "expected integer"),
    ("A3", "retention above 35 days", set_field("backup_retention_days", 36), "greater than the maximum"),
    ("A3", "DB rule on another port", add_rule(22, "10.0.1.25/32"), "must use the database port"),
]


# ---------------------------------------------------------------- tests

class CatalogueTests(unittest.TestCase):
    def test_one_function_per_catalogue_rule(self):
        self.assertEqual(set(RULES), set(catalogue_rules()))

    def test_every_task_rule_exists(self):
        for tid in SCORED:
            for rid in TASKS[tid]["scoring"]["rules"]:
                self.assertIn(rid, RULES, tid)

    def test_every_rule_has_a_negative_case(self):
        self.assertEqual({case[1] for case in NEGATIVE_CASES}, set(RULES))

    def test_finding_metadata_comes_from_catalogue(self):
        weights = load_catalogue()["severity_weights"]
        meta = catalogue_rules()
        for tid in SCORED:
            for finding in validate(TASKS[tid]["initial_config"], TASKS[tid]).findings:
                self.assertEqual(finding.severity, meta[finding.rule_id]["severity"])
                self.assertEqual(finding.weight, weights[finding.severity])
                self.assertEqual(finding.category, meta[finding.rule_id]["category"])


class TaskConfigTests(unittest.TestCase):
    def test_reference_solution_passes_every_rule(self):
        for tid in SCORED:
            with self.subTest(task=tid):
                result = validate(TASKS[tid]["reference_solution"], TASKS[tid])
                self.assertEqual(result.failed_rules, [])
                self.assertTrue(result.task_success)

    def test_initial_config_fails_exactly_traps_and_decision_points(self):
        for tid in SCORED:
            task = TASKS[tid]
            expected = {t["rule"] for t in task["design"]["traps"]} | \
                       {d["rule"] for d in task["design"]["decision_points"]}
            with self.subTest(task=tid):
                self.assertEqual(failing(task["initial_config"], task), expected)

    def test_pairs_score_identically(self):
        for a, b in zip(SETS["sets"]["A"], SETS["sets"]["B"]):
            ra = validate(TASKS[a]["initial_config"], TASKS[a]).to_dict()
            rb = validate(TASKS[b]["initial_config"], TASKS[b]).to_dict()
            for key in ("error_count", "weighted_score", "max_weighted_score", "task_success", "failed_rules"):
                self.assertEqual(ra[key], rb[key], f"{a}/{b} {key}")


class NegativeCaseTests(unittest.TestCase):
    def test_each_mistake_fails_exactly_one_rule(self):
        for resource, rule_id, description, mutate in NEGATIVE_CASES:
            for task in tasks_for(resource):
                with self.subTest(task=task["task_id"], case=description):
                    cfg = secure(task)
                    mutate(cfg, task["scoring"]["params"])
                    self.assertEqual(failing(cfg, task), {rule_id})

    def test_secure_variations_pass(self):
        for resource, description, mutate in POSITIVE_CASES:
            for task in tasks_for(resource):
                with self.subTest(task=task["task_id"], case=description):
                    cfg = secure(task)
                    mutate(cfg, task["scoring"]["params"])
                    self.assertEqual(failing(cfg, task), set())

    def test_two_different_ssh_mistakes_count_twice(self):
        task = TASKS["A1"]
        cfg = secure(task)
        add_rule(22, "0.0.0.0/0")(cfg, {})
        add_rule(22, "203.0.113.0/24")(cfg, {})
        self.assertEqual(failing(cfg, task), {"WS-01", "WS-02"})


class ScoringTests(unittest.TestCase):
    def test_initial_scores(self):
        expected = {  # (failed rules, weighted score, task success)
            "A1": ({"WS-01", "WS-06"}, 5, False),
            "A2": ({"ST-01", "ST-02", "ST-03", "ST-04"}, 8, False),
            "A3": ({"DB-01", "DB-03", "DB-05"}, 7, False),
        }
        for tid, (rules, score, success) in expected.items():
            result = validate(TASKS[tid]["initial_config"], TASKS[tid])
            self.assertEqual(set(result.failed_rules), rules, tid)
            self.assertEqual(result.error_count, len(rules), tid)
            self.assertEqual(result.weighted_score, score, tid)
            self.assertEqual(result.task_success, success, tid)

    def test_max_scores(self):
        for tid, maximum in {"A1": 12, "A2": 8, "A3": 12, "B1": 12, "B2": 8, "B3": 12}.items():
            self.assertEqual(validate(TASKS[tid]["reference_solution"], TASKS[tid]).max_weighted_score, maximum)

    def test_medium_failure_alone_is_still_task_success(self):
        task = TASKS["A1"]
        cfg = secure(task)
        cfg["disk_encryption"] = False
        result = validate(cfg, task)
        self.assertEqual((result.error_count, result.weighted_score, result.task_success), (1, 2, True))

    def test_result_dict_is_json_ready(self):
        result = validate(TASKS["A2"]["initial_config"], TASKS["A2"]).to_dict()
        json.dumps(result)
        self.assertEqual(result["errors_by_category"],
                         {"backup_recovery": 1, "encryption": 1, "logging": 1, "public_access": 1})
        self.assertEqual(result["task_success"], 0)


class SchemaTests(unittest.TestCase):
    def test_all_task_configs_are_valid(self):
        for tid in ALL_TASKS:
            for name in ("initial_config", "reference_solution"):
                with self.subTest(task=tid, config=name):
                    self.assertEqual(config_problems(TASKS[tid][name]), [])

    def test_invalid_configs_are_rejected(self):
        for tid, description, mutate, expected in INVALID_CASES:
            with self.subTest(case=description):
                cfg = secure(TASKS[tid])
                mutate(cfg, TASKS[tid]["scoring"]["params"])
                problems = config_problems(cfg)
                self.assertTrue(any(expected in p for p in problems), problems)
                with self.assertRaises(ConfigError):
                    validate(cfg, TASKS[tid])

    def test_wrong_resource_for_task(self):
        with self.assertRaises(ConfigError):
            validate(TASKS["A2"]["reference_solution"], TASKS["A1"])

    def test_practice_task_is_never_scored(self):
        with self.assertRaises(ValueError):
            validate(TASKS["P0"]["reference_solution"], TASKS["P0"])

    def test_unsupported_schema_keyword_is_an_error(self):
        with self.assertRaises(schema_module.SchemaError):
            schema_module._validate("x", {"format": "ipv4"}, "", {}, [])


class SubmissionTests(unittest.TestCase):
    def test_empty_decision_fields_block_submission(self):
        self.assertEqual(submission_problems(TASKS["A1"]["initial_config"]), [])
        self.assertEqual(len(submission_problems(TASKS["A2"]["initial_config"])), 2)
        self.assertEqual(submission_problems(TASKS["A3"]["initial_config"]),
                         ["ingress[0].source: fill in a source"])
        for tid in SCORED:
            self.assertEqual(submission_problems(TASKS[tid]["reference_solution"]), [], tid)

    def test_logging_on_needs_a_target(self):
        cfg = secure(TASKS["A2"])
        cfg["access_logging"]["target_bucket"] = ""
        self.assertEqual(submission_problems(cfg), ["access_logging.target_bucket: choose a target bucket"])


class IndependenceTests(unittest.TestCase):
    """The validator must not call the LLM or anything else outside the standard library."""

    ALLOWED = {"argparse", "collections", "dataclasses", "functools", "ipaddress", "json",
               "pathlib", "re", "sys", "validator"}

    def test_validator_imports_only_stdlib(self):
        for path in Path(__file__).parent.glob("*.py"):
            if path.name.startswith("test_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertIn(name.split(".")[0], self.ALLOWED, f"{path.name} imports {name}")


@unittest.skipIf(jsonschema is None, "jsonschema is not installed; cross-check skipped")
class JsonschemaCrossCheck(unittest.TestCase):
    """If the reference jsonschema library is available, it must agree with our checker."""

    def test_same_verdicts(self):
        schema = schema_module.load_schema()
        checker = jsonschema.Draft202012Validator(schema)
        configs = [TASKS[tid][name] for tid in ALL_TASKS for name in ("initial_config", "reference_solution")]
        for tid, _, mutate, _ in INVALID_CASES:
            cfg = secure(TASKS[tid])
            mutate(cfg, TASKS[tid]["scoring"]["params"])
            configs.append(cfg)
        for cfg in configs:
            self.assertEqual(not schema_module.schema_problems(cfg), checker.is_valid(cfg), json.dumps(cfg))


if __name__ == "__main__":
    unittest.main()
