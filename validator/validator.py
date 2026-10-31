"""Scores a submitted configuration against one task's security rules.

    from validator import load_task, validate
    result = validate(config, load_task("A1"))
    result.error_count, result.weighted_score, result.task_success
    result.to_dict()        # what the event log (Step 6) stores

From the command line (prints the result as JSON):

    python -m validator A1 path/to/config.json

The validator reads only the configuration and the task's scoring block. It never calls the
LLM, never reads the LLM's output, and imports nothing outside the standard library and this
package (test_rules.py checks this).
"""
import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from validator.rules import RULES
from validator.schema import ConfigError, check_config

VALIDATOR_VERSION = "1.0"
ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "tasks"
CATALOGUE_PATH = TASKS_DIR / "rules_catalogue.json"


@lru_cache(maxsize=1)
def load_catalogue():
    catalogue = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8-sig"))
    ids = {rule["id"] for rule in catalogue["rules"]}
    if ids != set(RULES):
        raise RuntimeError(f"catalogue and rules.py disagree: no function for {sorted(ids - set(RULES))}, "
                           f"not in catalogue {sorted(set(RULES) - ids)}")
    return catalogue


def catalogue_rules():
    return {rule["id"]: rule for rule in load_catalogue()["rules"]}


def load_task(task_id):
    return json.loads((TASKS_DIR / f"task_{task_id}.json").read_text(encoding="utf-8-sig"))


@dataclass(frozen=True)
class Finding:
    rule_id: str
    title: str
    severity: str
    weight: int
    category: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    task_id: str
    resource: str
    rules_checked: tuple
    findings: tuple
    max_weighted_score: int

    @property
    def failed_rules(self):
        return [f.rule_id for f in self.findings]

    @property
    def passed_rules(self):
        failed = set(self.failed_rules)
        return [rid for rid in self.rules_checked if rid not in failed]

    @property
    def error_count(self):
        """Build plan metric: number of failed validator rules."""
        return len(self.findings)

    @property
    def weighted_score(self):
        """Build plan metric: sum of severity weights of failed rules (lower is better)."""
        return sum(f.weight for f in self.findings)

    @property
    def high_failures(self):
        return sum(1 for f in self.findings if f.severity == "high")

    @property
    def task_success(self):
        """Build plan metric: 1 if zero high-severity failures, else 0."""
        return self.high_failures == 0

    @property
    def errors_by_category(self):
        return dict(sorted(Counter(f.category for f in self.findings).items()))

    def to_dict(self):
        return {
            "validator_version": VALIDATOR_VERSION,
            "task_id": self.task_id,
            "resource": self.resource,
            "error_count": self.error_count,
            "weighted_score": self.weighted_score,
            "max_weighted_score": self.max_weighted_score,
            "high_failures": self.high_failures,
            "task_success": int(self.task_success),
            "errors_by_category": self.errors_by_category,
            "failed_rules": self.failed_rules,
            "passed_rules": self.passed_rules,
            "findings": [asdict(f) for f in self.findings],
        }


def validate(config, task):
    """Score one configuration. Raises ConfigError if the configuration itself is invalid."""
    if not task.get("scored"):
        raise ValueError(f"task {task.get('task_id')} is not scored (practice tasks are never validated)")
    check_config(config)
    if config["resource"] != task["resource"]:
        raise ConfigError([f"resource: task {task['task_id']} expects {task['resource']}, "
                           f"got {config['resource']}"])

    rules = catalogue_rules()
    weights = load_catalogue()["severity_weights"]
    params = task["scoring"]["params"]
    rule_ids = tuple(task["scoring"]["rules"])
    findings = []
    for rid in rule_ids:
        message = RULES[rid](config, params)
        if message is not None:
            meta = rules[rid]
            findings.append(Finding(rid, meta["title"], meta["severity"], weights[meta["severity"]],
                                    meta["category"], message))
    return ValidationResult(
        task_id=task["task_id"],
        resource=task["resource"],
        rules_checked=rule_ids,
        findings=tuple(findings),
        max_weighted_score=sum(weights[rules[rid]["severity"]] for rid in rule_ids),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score a configuration JSON file against a task.")
    parser.add_argument("task_id", help="for example A1")
    parser.add_argument("config", help="path to the configuration JSON")
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    try:
        result = validate(config, load_task(args.task_id))
    except ConfigError as exc:
        print(json.dumps({"config_errors": exc.problems}, indent=2))
        return 2
    print(json.dumps(result.to_dict(), indent=2))
    return 0
