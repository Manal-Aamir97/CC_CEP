# Step 7 cases: real human-written configurations

Each case is one resource that a real person wrote in Terraform, taken from a fetched project (`dataset/raw/repos/`) and converted **by hand** into our JSON schema. Cases replace the plan's "configurations with planted errors" with real human mistakes, plus some clean configurations, so the LLM's advice can be checked against the validator on real data.

Target: 30 cases. For each of the three resource types, 7 from projects whose 2022 scan failed a mapped check and 3 from projects that passed (see `dataset/results/case_candidates.csv`). If a project is gone, can't be converted, or turns out to be a demo, test suite or course rather than a real configuration, use a reserve from the same group and record why. Decide this from the project's purpose, before looking at its validator score. Start from the worksheet in `dataset/results/worksheets/`, and see the "Check these before converting" list in `dataset/README.md`.

## File layout

Save as `case_C01.json`, `case_C02.json`, and so on. It is a task file (same keys as `tasks/task_A1.json`, so `validator.validate()` scores it unchanged) plus a `provenance` block:

```json
{
  "task_id": "C01",
  "set": "offline",
  "scored": true,
  "resource": "web_server",
  "participant": {
    "title": "Review an existing web server",
    "scenario": "This server was written by a team for their own project. Review its configuration against the requirements below.",
    "requirements": [
      {"id": "R1", "text": "SSH (port 22) must not be reachable from the internet."},
      {"id": "R2", "text": "The server's disk must be encrypted."}
    ],
    "notes": ""
  },
  "scoring": {
    "rules": ["WS-01", "WS-06"],
    "params": {},
    "requirement_map": {"R1": ["WS-01"], "R2": ["WS-06"]}
  },
  "initial_config": { "...": "the converted resource, valid against schema/config.schema.json" },
  "provenance": {
    "repo": "owner/name",
    "commit": "full 40-character SHA from dataset/raw/repos/MANIFEST.json",
    "commit_date": "from the manifest",
    "path": "path/inside/the/repo/main.tf",
    "lines": "12-58",
    "converted_by": "initials of the person who converted it",
    "conversion_notes": "every assumption, default and dropped rule, in plain words"
  }
}
```

`python -m dataset.check_cases` checks every case:

- the layout and schema;
- that each rule fits the resource type;
- that every rule maps to exactly one requirement;
- that the commit and file really are among the fetched files.

It then scores each case as written.

## Conversion rules

1. **One unit per case.**
   - *web_server*: one `aws_instance` (or launch template) plus the ingress rules of the security groups attached to it, plus root-volume encryption.
   - *storage_bucket*: one `aws_s3_bucket` plus its related resources. Older provider versions use inline blocks; version 4 and later use separate resources (`aws_s3_bucket_public_access_block`, `_versioning`, `_logging`, `_server_side_encryption_configuration`).
   - *database*: one `aws_db_instance` plus the ingress rules on its port.
2. **Copy values exactly as written.** Never fix, tidy or "improve" anything.
3. **Absent attributes.** Use the default stated in the AWS provider documentation for that attribute and cite it in `conversion_notes`. If the default can't be confirmed, drop the rule that depends on it.
4. **Values from variables or modules.** Use the variable's default if the same project declares one. Otherwise drop the rules that depend on the value and say so. Never guess.
5. **Rules that need intent are kept only when the project states it:**
   - WS-02 needs a named administrator address.
   - DB-03 needs the application server's address.
   - WS-04 and WS-05 apply only to a clearly public website.
   - ST-04's target bucket must be named in the project.
6. **Unscored fields** (name, instance type, OS, engine, region): pick the nearest allowed schema value and note it.
7. **Check each other's work.** A second person converts at least 10 cases independently. Report how often the two converted configurations agree, field by field, and settle differences before scoring.

## Requirement wording (use only the lines for rules the case keeps)

| Rule | Requirement text |
|---|---|
| WS-01 | SSH (port 22) must not be reachable from the internet. |
| WS-02 | Only the administrator at `<admin address>` may connect over SSH (port 22). |
| WS-03 | Remote Desktop (RDP, port 3389) must not be reachable from the internet. |
| WS-04 / WS-05 | Visitors anywhere on the internet must be able to open the website over HTTP (port 80) / HTTPS (port 443). |
| WS-06 | The server's disk must be encrypted. |
| ST-01 | Block all public access to the bucket. |
| ST-02 | Stored objects must be encrypted at rest. |
| ST-03 | Earlier versions of each object must be kept, so deleted or overwritten objects can be recovered. |
| ST-04 | Every request made to the bucket must be logged to the log bucket `<name>`. |
| DB-01 | The database must not be publicly accessible. |
| DB-02 | The database port must not be reachable from the internet. |
| DB-03 | Only the application server at `<address>` may connect to the database port. |
| DB-04 | The database storage must be encrypted. |
| DB-05 | Automatic backups must be kept for at least 7 days. |

## How each case is used in Step 7

1. **Without LLM:** the validator scores `initial_config`, the configuration exactly as the human wrote it.
2. **The LLM's turn:** the LLM receives the Step 5 prompt with the case's requirements and configuration. It never sees the findings, the provenance, or the fact that the case is real.
3. **With LLM:** apply exactly what the LLM recommends, no more and no less, then score the result with the validator again.
4. **What to record:**
   - errors the LLM caught;
   - errors it missed;
   - advice that was wrong (it introduced a new failure or broke a requirement);
   - the change in error count and weighted score.
