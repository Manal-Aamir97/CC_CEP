# Step 3: Rule-based security validator

The validator is the study's measuring instrument. It takes the configuration JSON a participant submits and a task, runs that task's rules, and returns the build plan's metrics. It never calls the LLM, never reads the LLM's output, and imports nothing outside the Python standard library. A test enforces this.

| File | Contents |
|---|---|
| `schema.py` | Checks a config against `schema/config.schema.json` (stdlib only), plus CIDR and database-port checks, plus `submission_problems` for the form |
| `rules.py` | One function per rule in `tasks/rules_catalogue.json`. Each takes `(cfg, params)` and returns `None` (pass) or a message (fail). |
| `validator.py` | `validate(cfg, task)`: runs the rules, attaches severity and weight from the catalogue, computes the metrics |
| `test_rules.py` | Unit tests (below) |

## Use

```python
from validator import load_task, validate, ConfigError

result = validate(config, load_task("A1"))   # raises ConfigError if the config itself is invalid
result.error_count       # number of failed rules
result.weighted_score    # sum of severity weights of failed rules (0 = perfect)
result.task_success      # True if no high-severity rule failed
result.to_dict()         # JSON-ready; this is what the event log (Step 6) stores
```

From the command line, run from the repository root:

```bash
python -m validator A1 path/to/config.json
```

## Metrics (build plan, "Metrics")

| `to_dict()` key | Build plan metric |
|---|---|
| `error_count` | Error count: number of failed validator rules |
| `weighted_score` | Weighted error score: sum of severity weights (high 3, medium 2, low 1) |
| `task_success` | Task success: 1 if zero high-severity failures, else 0 |
| `errors_by_category` | Breakdown by rule category for Section 4 (network, public access, encryption, ...) |
| `failed_rules`, `findings` | Which rules failed, with a message each, for the advice-induced-error check |

Maximum weighted score per task: web server 12, storage bucket 8, database 12.

## How the rules avoid double counting

WS-02 and DB-03 share one helper. It fails when the allowed address cannot connect, or when a source other than that address is present and is not world-open. World-open sources (`0.0.0.0/0`, `::/0`) are left to WS-01 and DB-02. So "SSH open to the world" costs 3 points once, not 3 + 2. Two different mistakes still count twice: a world rule plus a /24 rule fails both WS-01 and WS-02.

## Tests

```bash
python -m unittest validator.test_rules -v
```

| Test group | What it proves |
|---|---|
| Catalogue | Exactly one function per catalogue rule, every rule has negative cases, and severity and weight always come from the catalogue |
| Task configs | Every `reference_solution` passes all its rules. Every `initial_config` fails exactly its traps plus its decision points. A and B tasks score identically. |
| Negative cases | 36 hand-made insecure configs, each run on both tasks of its pair (72 checks), each failing exactly one rule: the one it targets |
| Secure variations | 6 secure edits (e.g. SSE-KMS, 35-day backups, dual-stack HTTP), each run on both tasks of its pair, that must pass every rule |
| Scoring | Error counts, weighted scores, task success and maximum scores for known configs |
| Schema | All task configs are valid; 15 kinds of invalid config are rejected; practice tasks and resource mismatches are refused |
| Submission | Empty decision fields block Submit, and complete configs do not |
| Independence | Validator modules import only the standard library and the validator package, so they cannot reach an LLM |
| jsonschema cross-check | If the `jsonschema` package is installed, it must agree with `schema.py` on every config. The test is skipped when the package is missing. |

Before the study, the tests were also run against six deliberately planted bugs (for example a rule that never fails, a world-open source counted twice, an off-by-one in the backup check, and a network import). All six made the tests fail.

## Text you can adapt for Section 3

> Submitted configurations were scored by a deterministic, rule-based validator implemented in Python. Each of the 15 rules is a separate function that inspects one aspect of the configuration and returns pass or fail, a severity, and a message. Rule severities were taken from the AWS Security Hub CSPM severity of the closest matching control (Critical or High = 3, Medium = 2, Low = 1). Where a rule has a matching requirement in the CIS AWS Foundations Benchmark (v5.0.0 / v3.0.0), it is listed in Table X. The validator never calls the LLM, never receives its output, and has no network access. It scores only the JSON configuration submitted by the participant. Its correctness was checked with unit tests: every task's reference solution passes all rules, every task's starting configuration fails exactly the planted traps, and 36 hand-built insecure configurations each fail exactly the one rule they target.

(Before citing CIS or AWS documentation, see the note at the end of `tasks/README.md` about the instructor's reference rules.)
