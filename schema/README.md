# Step 2: Configuration schema

[`config.schema.json`](config.schema.json) fixes the JSON shape of each resource type. It is standard JSON Schema (draft 2020-12), so any JSON Schema library can use it. In Python, `validator/schema.py` checks it using only the standard library.

Every other part of the system reads this shape:

| Component | How it uses the schema |
|---|---|
| Form (Step 4) | Builds this JSON on every change, and can check it in the browser with any JSON Schema library |
| Validator (Step 3) | Rejects anything that does not match before scoring (`check_config`) |
| LLM prompt (Step 5) | CURRENT CONFIGURATION is this JSON, unchanged |
| Event log (Step 6) | Field changes are recorded as paths into this JSON, e.g. `ingress[0].source` |
| Task files (Step 1) | Every `initial_config` and `reference_solution` must match it (`tasks/check_tasks.py` checks this) |

## Three levels of "valid"

| Level | Meaning | Checked by |
|---|---|---|
| **Draft-valid** | Right fields and types. Decision fields may still be empty (`null` or `""`). | `config_problems(cfg)` returns `[]` |
| **Submittable** | Draft-valid, and nothing is left empty. The form must block Submit until this holds. It never says which value is secure. | `submission_problems(cfg)` returns `[]` |
| **Scored** | The validator's verdict on a submittable config. | `validate(cfg, task)` |

The validator can also score a draft config; empty values count as failing. The Step 3 tests rely on this.

## Fields

### `web_server` (EC2-like)

| Field | Allowed values | Scored by |
|---|---|---|
| `resource` | `"web_server"` | - |
| `name` | lowercase letters, digits, hyphens (1-63) | not scored |
| `instance_type` | `t2.micro`, `t2.small`, `t3.micro`, `t3.small`, `t3.medium` | not scored |
| `os` | `ubuntu-22.04`, `ubuntu-24.04`, `debian-12`, `amazon-linux-2023` | not scored |
| `ingress` | up to 10 ingress rules (below) | WS-01 to WS-05 |
| `disk_encryption` | `true` / `false` | WS-06 |
| `public_ip` | `true` / `false` | not scored |

### `storage_bucket` (S3-like)

| Field | Allowed values | Scored by |
|---|---|---|
| `resource` | `"storage_bucket"` | - |
| `bucket_name` | S3 naming: lowercase, digits, dots, hyphens (3-63) | not scored |
| `region` | `us-east-1`, `eu-west-1`, `eu-west-2`, `ap-south-1` | not scored |
| `block_public_access` | object with four booleans: `block_public_acls`, `ignore_public_acls`, `block_public_policy`, `restrict_public_buckets` | ST-01 |
| `encryption` | `"none"`, `"sse-s3"`, `"sse-kms"`, or `null` (not chosen yet) | ST-02 |
| `versioning` | `"enabled"`, `"disabled"`, or `null` (not chosen yet) | ST-03 |
| `access_logging` | `{ "enabled": bool, "target_bucket": bucket name or "" }` | ST-04 |

### `database` (RDS-like PostgreSQL)

| Field | Allowed values | Scored by |
|---|---|---|
| `resource` | `"database"` | - |
| `identifier` | starts with a letter; lowercase, digits, hyphens (1-63) | not scored |
| `engine` | `postgres-15`, `postgres-16`, `postgres-17` | not scored |
| `instance_class` | `db.t3.micro`, `db.t3.small`, `db.t4g.micro`, `db.t4g.small` | not scored |
| `port` | integer 1150-65535 (the form shows it read-only) | used by DB-02, DB-03 |
| `publicly_accessible` | `true` / `false` | DB-01 |
| `ingress` | up to 10 ingress rules, every one on the database port | DB-02, DB-03 |
| `storage_encrypted` | `true` / `false` | DB-04 |
| `backup_retention_days` | integer 0-35; 0 switches automated backups off | DB-05 |

### Ingress rule (shared by `web_server` and `database`)

| Field | Allowed values |
|---|---|
| `port` | integer 1-65535 |
| `protocol` | `"tcp"` |
| `source` | an IPv4 or IPv6 CIDR block (`203.0.113.25/32`, `0.0.0.0/0`, `::/0`), or `""` while not filled in |

## Checks the schema cannot express

`validator/schema.py` adds two checks after the schema passes:

- every non-empty `source` must parse as a CIDR block (`999.0.0.1/32` matches the pattern but is rejected);
- every `database` ingress rule must use the database's own `port`.

## What the form must do (Step 4)

- Turn a bare address into a CIDR before building the JSON: `203.0.113.25` becomes `203.0.113.25/32` (IPv6: `/128`). Trim spaces.
- Use `null` for an unchosen `encryption` or `versioning` and `""` for an empty `source`, and show a "Select..." placeholder for them.
- Block Submit while `submission_problems(cfg)` is not empty, in both conditions.

## Simplifications to state in Section 3

These keep the study small and are the same in both conditions:

- Only TCP, and one port per rule (no port ranges, no "all traffic" option).
- "The internet" means `0.0.0.0/0` or `::/0` exactly, matching the CIS and Security Hub checks. A source with host bits set, such as `203.0.113.25/24`, is read as its network (`203.0.113.0/24`).
- Only Linux operating systems are offered for the web server.
- The bucket's `encryption` field offers `none`, although the real service now encrypts new buckets with SSE-S3 unless told otherwise (see `tasks/README.md`).

## Changing the schema

The `$id` ends in `v1`. If a field is added, removed or renamed: bump the version in `$id`, update the task files, rerun `python tasks/check_tasks.py` and `python -m unittest validator.test_rules`, and do not change it once the pilot (Step 8) has started.
