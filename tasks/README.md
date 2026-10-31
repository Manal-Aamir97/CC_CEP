# Step 1: Configuration tasks

This folder is Step 1 of the CEP build plan. It holds the tasks participants complete, the security rules each task is scored on, and the insecure defaults ("traps") planted in each form. Everything later reads these files:

- the form (Step 4) loads `initial_config` as the starting state,
- the LLM prompt (Step 5) gets the `participant` text as TASK REQUIREMENTS,
- the validator (Step 3) implements `rules_catalogue.json` and scores with each task's `scoring` block,
- the validator's unit tests use `reference_solution` (must pass every rule) and `initial_config` (must fail the trap and decision-point rules).

## Files

| File | Contents |
|---|---|
| `rules_catalogue.json` | The 15 security rules: what makes each one fail, severity and weight, category, CIS and AWS Security Hub mapping |
| `task_A1.json` to `task_B3.json` | The six scored tasks (three matched pairs) |
| `task_P0.json` | The unscored practice task for the 3-minute walkthrough |
| `sets.json` | Set A and Set B, their fixed order, and the four counterbalancing groups from the build plan |
| `check_tasks.py` | Checks every file for consistency and checks that each A/B pair is matched; writes `TASKS.md` |
| `TASKS.md` | Generated task sheet for reading and for writing Section 3. Do not edit it by hand. |

Run from the repository root (Python 3, standard library only):

```bash
python tasks/check_tasks.py --write-sheet
```

Run it after any edit to a task file. It exits with an error and lists the problems if anything is inconsistent.

## The six tasks

| Pair | Resource | Set A | Set B | What the participant must achieve | Traps (pre-filled insecure values) |
|---|---|---|---|---|---|
| 1 | Web server (EC2-like) | A1 Crumb & Co. Bakery ordering site | B1 Northfield Community Library events site | HTTP and HTTPS open to everyone; SSH only from one admin IP; no public RDP; disk encrypted | SSH open to `0.0.0.0/0`; disk encryption off |
| 2 | Storage bucket (S3-like) | A2 Riverside Physiotherapy Clinic referral letters | B2 Westbrook College exam papers | Block all public access; encryption at rest; versioning on; access logging to a named log bucket | 2 of the 4 Block Public Access switches off; access logging off |
| 3 | Database (RDS-like) | A3 Paperleaf Books orders | B3 Harbour Street Gym members | Not publicly accessible; DB port only from the app server; storage encrypted; backups kept 7+ days | Publicly accessible = yes; backup retention 0 days |

The full participant text, the rules each requirement maps to, and the edits needed for a secure answer are in [TASKS.md](TASKS.md).

## Design decisions

1. **The pairs are isomorphic.** In each pair, A and B use the same resource, form, rules, traps on the same fields, and number of edits needed. Only the story, names and addresses change. This is the most direct way to make the two sets equally hard. `check_tasks.py` enforces it: for each pair it compares rules, severity mix, maximum score, trap fields, decision points, required edits and text length (within 15%). The pilot (Step 8) still has to confirm the match with real people, and Section 4 should compare Set A and Set B scores directly.
   *Cost:* a participant may recognise the second task of a pair. Counterbalancing (`sets.json`) spreads this learning effect evenly over both conditions, and the build plan already asks for the order effect to be reported.

2. **Two traps per task: one high, one medium.** Each trap is an insecure value already filled in, with a real-world basis (table below). Every task's traps are worth the same, 3 + 2 = 5 points, so traps are comparable across resource types as well as within pairs.

3. **The story explains the pre-filled values.** Every task says the form was pre-filled from an earlier template or bucket. Real consoles do this (launch templates, "copy settings from existing bucket"). The sentence is the same in both conditions, and it does not say which fields are wrong.

4. **Three kinds of starting value.** Each task's `design` block lists every scored field as one of:
   - *trap*: insecure, must be changed;
   - *decision point*: empty, the participant must choose (the form will not submit until they do);
   - *secure default*: already correct, must not be broken.

5. **Functional rules catch over-hardening.** A form with everything locked down is not a correct answer. WS-04 and WS-05 fail if the website is no longer reachable. Part (a) of WS-02 and of DB-03 fails if the administrator or the application can no longer connect. This matters for the LLM: advice like "close port 80 and use only HTTPS" contradicts the task, and the validator will count it as an error. WS-04/05 are in their own category (`functional`) so Section 4 can report them separately.

6. **One mistake, one count.** Where a high rule and a medium rule check the same field, a world-open source counts only under the high rule (WS-01, DB-02). The medium rule (WS-02, DB-03) covers every other wrong value: a /24 instead of a /32, a mistyped address, a missing rule. A missing or unchosen value always fails.

7. **Severity comes from AWS, not from us.** Each rule takes the severity AWS Security Hub CSPM gives its closest control: Critical or High = high (3), Medium = medium (2), Low = low (1). The four rules with no matching control (WS-02, WS-04, WS-05, DB-03) are marked `researcher-assigned`. This gives Section 3 a stated, checkable basis for the weights instead of a judgement call.

8. **Safe, fictional details.** All organisations are made up. Public addresses come from the documentation-only ranges reserved by RFC 5737 (`203.0.113.0/24`, `198.51.100.0/24`), and private addresses from `10.0.0.0/8`. Nothing points at a real network.

9. **Fixed order within a set.** The order is always web server, then bucket, then database, so each task sits in the same position in both conditions.

10. **Neutral fields are never scored.** Instance type, OS, names, region and engine are on the form for realism only (`unscored_fields`).

## Rules and standards mapping

All mappings were checked on 2026-10-18 against the AWS Security Hub CSPM User Guide, which lists the CIS AWS Foundations Benchmark requirement behind each control. Security Hub supports CIS v5.0.0, v3.0.0, v1.4.0 and v1.2.0 and recommends v5.0.0. The catalogue records both v5.0.0 and v3.0.0 numbers, so Section 3 can cite whichever version you choose.

| Rule | Check | Weight | CIS v5.0.0 / v3.0.0 | Security Hub |
|---|---|---|---|---|
| WS-01 | SSH (22) not open to `0.0.0.0/0` or `::/0` | 3 | 5.3, 5.4 / 5.2, 5.3 | EC2.53, EC2.54 (High) |
| WS-02 | SSH only from the admin /32, admin not locked out | 2 | - | researcher-assigned |
| WS-03 | RDP (3389) not open to the internet | 3 | 5.3, 5.4 / 5.2, 5.3 | EC2.53, EC2.54 (High) |
| WS-04 | HTTP (80) open to the internet | 1 | - | researcher-assigned |
| WS-05 | HTTPS (443) open to the internet | 1 | - | researcher-assigned |
| WS-06 | Disk encrypted | 2 | 5.1.1 / 2.2.1 (related: account-wide default) | EC2.3, EC2.7 (Medium) |
| ST-01 | All four Block Public Access switches on | 3 | 2.1.4 / 2.1.4 | S3.8 (High) |
| ST-02 | Encrypted at rest (SSE-S3 or SSE-KMS) | 2 | - | S3.17 (Medium, stricter: KMS only) |
| ST-03 | Versioning enabled | 1 | - | S3.14 (Low) |
| ST-04 | Access logging on, to the named log bucket | 2 | - (3.4 covers only the CloudTrail bucket) | S3.9 (Medium) |
| DB-01 | Not publicly accessible | 3 | 2.2.3 / 2.3.3 | RDS.2 (Critical) |
| DB-02 | DB port not open to the internet | 3 | - | EC2.19 (Critical; its high-risk list includes 5432) |
| DB-03 | DB port only from the app server /32, app not cut off | 2 | - | researcher-assigned |
| DB-04 | Storage encrypted | 2 | 2.2.1 / 2.3.1 | RDS.3 (Medium) |
| DB-05 | Backup retention of at least 7 days | 2 | - | RDS.11 (Medium; default minimum 7 days) |

Maximum weighted score per task: web server 12, bucket 8, database 12; 32 per set.

A note on ST-02: in the real service, new buckets are encrypted with SSE-S3 unless told otherwise, so "none" is not a real option today. The simulator offers it on purpose, to test whether participants recognise the requirement. Mention this as a simulation simplification in Section 3.

## Traps and why they are realistic

| Task pair | Trap | Real-world basis |
|---|---|---|
| A1 / B1 | SSH source `0.0.0.0/0` | When the EC2 launch wizard creates a new security group it adds an SSH (Linux) or RDP (Windows) rule open to all IP addresses, to be narrowed by the user ([EC2 instance configuration parameters](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-launch-parameters.html)). |
| A1 / B1 | Disk encryption off | New EBS volumes are unencrypted unless "EBS encryption by default" is on for the account ([Security Hub EC2.3 / EC2.7](https://docs.aws.amazon.com/securityhub/latest/userguide/ec2-controls.html)). |
| A2 / B2 | `block_public_policy` and `restrict_public_buckets` off | Settings copied from a bucket that serves a public website through a bucket policy, which needs exactly these two switches off. |
| A2 / B2 | Access logging off | Server access logging is off until switched on ([Security Hub S3.9](https://docs.aws.amazon.com/securityhub/latest/userguide/s3-controls.html)). |
| A3 / B3 | Publicly accessible = yes | Copied from a developers' test template. The real RDS console defaults this to No, so the trap comes from the template, not from the console. |
| A3 / B3 | Backup retention 0 days | Copied from the test template to save cost. A retention of 0 switches RDS automated backups off ([Security Hub RDS.11](https://docs.aws.amazon.com/securityhub/latest/userguide/rds-controls.html)). |

## Who sees what

| Part of a task file | Participant's browser | LLM prompt | Validator |
|---|---|---|---|
| `display` | Yes, as the task text, in plain words for someone with no cloud knowledge | No | No |
| `participant` | No | Yes, as TASK REQUIREMENTS, in exact technical words | No |
| `initial_config` | Yes, as the starting form, shown through plain labels | Yes, as CURRENT CONFIGURATION once edited | Scores the submitted version |
| `scoring` | No | No | Yes |
| `design` | No | No | No (documentation and tests only) |
| `reference_solution` | No | No | Unit tests only |

The app must remove `scoring`, `design` and `reference_solution` before anything reaches the browser or the LLM. If the LLM saw the rules or the traps, the study would measure the answer key, not the LLM. This follows the same reasoning as the build plan's rule that the LLM never sees validator findings. `check_tasks.py` also checks that neither wording contains a rule ID, the word "trap", or a trap's value. It also checks that `display` has the same requirements as `participant` and gives the same addresses and names, and that A and B stay about the same length in both wordings.

The two wordings must ask for the same things. If you change a requirement, change both.

## What later steps take from here

- **Step 2 (schema): done.** See [`schema/README.md`](../schema/README.md). `check_tasks.py` now also checks every `initial_config` and `reference_solution` against the schema.
- **Step 3 (validator): done.** See [`validator/README.md`](../validator/README.md). Each `reference_solution` passes all of its task's rules, each `initial_config` fails exactly its trap and decision-point rules, and 36 hand-made insecure configs each fail exactly the rule they target.
- **Step 4 (form):** decision points need a "Select..." placeholder and must block submission until chosen. The ingress table needs add, remove and edit for each row. Normalise a bare IP such as `203.0.113.25` to `/32` when it is typed. Show the database port as read-only.
- **Step 5 (LLM prompt):** TASK REQUIREMENTS = participant title + scenario + numbered requirements + notes, exactly as shown in `TASKS.md`.

## Check in the pilot (Step 8)

- Can participants follow every requirement without help? If two or more pilot participants misread one, reword it in both sets and run the checker again.
- Do A and B tasks take similar time and produce similar error counts?
- Does the "pre-filled from a template" sentence make the traps too obvious, or not noticeable enough?
- Is a time limit per task needed to keep the whole session near 40 minutes?

## Citing the standard in Section 3

The CIS AWS Foundations Benchmark is an industry standard, and the AWS Security Hub guide is vendor documentation. Neither is a peer-reviewed journal article, so neither meets the instructor's Web of Science / Impact Factor rule for the bibliography. Before Section 3 is written, ask the instructor whether standards can be cited separately (for example as a footnote or in a "standards" list). If not, describe the mapping in the text without a numbered reference.
