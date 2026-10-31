"""Test configurations for the offline check of the assistant (build plan Step 7).

planted_cases(): 30 configurations with known, planted errors, built from our own tasks:
    each task's starting form (its traps) + 4 single-error versions of its reference answer.
real_cases(): the hand-converted human configurations in dataset/cases/ (may be empty).

The ground truth for every case is the validator's verdict, never a hand-written label.
"""
import copy
import json
from pathlib import Path

from app import study
from validator import validate

ROOT = Path(__file__).resolve().parents[1]

# One mistake each, applied to a task's reference answer. Values are what a participant could pick.
MUTATIONS = {
    "web_server": {
        "ssh_open_to_internet": lambda c, p: _set_source(c, 22, "0.0.0.0/0"),
        "rdp_rule_added": lambda c, p: c["ingress"].append({"port": 3389, "protocol": "tcp", "source": "0.0.0.0/0"}),
        "https_rule_removed": lambda c, p: c.update(ingress=[r for r in c["ingress"] if r["port"] != 443]),
        "disk_not_encrypted": lambda c, p: c.update(disk_encryption=False),
    },
    "storage_bucket": {
        "public_policy_not_blocked": lambda c, p: c["block_public_access"].update(block_public_policy=False),
        "no_encryption": lambda c, p: c.update(encryption="none"),
        "versioning_disabled": lambda c, p: c.update(versioning="disabled"),
        "logs_into_same_bucket": lambda c, p: c["access_logging"].update(target_bucket=c["bucket_name"]),
    },
    "database": {
        "publicly_accessible": lambda c, p: c.update(publicly_accessible=True),
        "port_open_to_internet": lambda c, p: _set_source(c, c["port"], "0.0.0.0/0"),
        "port_open_to_whole_network": lambda c, p: _set_source(c, c["port"], "10.0.0.0/16"),
        "backups_off": lambda c, p: c.update(backup_retention_days=0),
    },
}


def _set_source(cfg, port, source):
    for rule in cfg["ingress"]:
        if rule["port"] == port:
            rule["source"] = source


def _case(case_id, task_id, source, config):
    task = study.task(task_id)
    result = validate(config, task)
    return {"case_id": case_id, "source": source, "task_id": task_id, "task": study.public_task(task_id),
            "config": config, "scoring_task": task, "expected_failures": result.failed_rules,
            "findings": [f.__dict__ for f in result.findings]}


def planted_cases():
    cases = []
    for task_id in study.sets()["sets"]["A"] + study.sets()["sets"]["B"]:
        task = study.task(task_id)
        cases.append(_case(f"planted-{task_id}-starting-form", task_id, "planted",
                           copy.deepcopy(task["initial_config"])))
        for name, mutate in MUTATIONS[task["resource"]].items():
            config = copy.deepcopy(task["reference_solution"])
            mutate(config, task["scoring"]["params"])
            cases.append(_case(f"planted-{task_id}-{name}", task_id, "planted", config))
    return cases


def real_cases():
    """Hand-converted human cases (dataset/cases/case_*.json); each is scored by its own rules."""
    cases = []
    for path in sorted((ROOT / "dataset" / "cases").glob("case_*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        public = {k: case[k] for k in ("task_id", "resource", "scored", "participant")}
        public["initial_config"] = case["initial_config"]
        result = validate(case["initial_config"], case)
        cases.append({"case_id": f"real-{case['task_id']}", "source": "real", "task_id": case["task_id"],
                      "task": public, "config": case["initial_config"], "scoring_task": case,
                      "expected_failures": result.failed_rules, "findings": [f.__dict__ for f in result.findings],
                      "provenance": case["provenance"]})
    return cases
