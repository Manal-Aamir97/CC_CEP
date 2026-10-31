"""Checks the Step 1 task files and writes the readable task sheet.

Run from the repository root:

    python tasks/check_tasks.py                 # run the checks
    python tasks/check_tasks.py --write-sheet   # run the checks, then write tasks/TASKS.md

Exit code 0 means every check passed. Uses the Python standard library only; every starting
form and reference solution is also checked against schema/config.schema.json.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHEET = HERE / "TASKS.md"

sys.path.insert(0, str(HERE.parent))
from validator.schema import config_problems  # noqa: E402  (needs the repository root on the path)

RESOURCE_PREFIX = {"web_server": "WS", "storage_bucket": "ST", "database": "DB"}
WEIGHTS = {"high": 3, "medium": 2, "low": 1}
MAX_TRAPS = 2              # build plan, Step 1: "1 or 2 traps per task"
MIN_WORD_RATIO = 0.85      # A and B task texts may differ in length by at most 15%
TASK_KEYS = {"task_id", "set", "pair", "order_in_set", "scored", "resource",
             "participant", "display", "scoring", "initial_config", "design", "reference_solution"}
RULE_KEYS = {"id", "resource", "title", "category", "severity", "params", "fails_when",
             "precedence", "cis", "cis_relation", "security_hub", "severity_source"}
RULE_ID = re.compile(r"\b[A-Z]{2}-\d{2}\b")
PATH_TOKEN = re.compile(r"[^.\[\]]+|\[[^\]]*\]")
MISSING = object()


# ---------------------------------------------------------------- helpers

def same(a, b):
    """Type-strict equality (keeps False distinct from 0)."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def get_path(obj, path):
    """Resolve 'a.b', 'ingress[0].source' or 'ingress[port=22].source'."""
    for token in PATH_TOKEN.findall(path):
        if token.startswith("["):
            selector = token[1:-1]
            if "=" in selector:
                key, wanted = selector.split("=", 1)
                hits = [item for item in obj if str(item.get(key)) == wanted]
                if len(hits) != 1:
                    raise KeyError(f"[{selector}] matched {len(hits)} items")
                obj = hits[0]
            else:
                obj = obj[int(selector)]
        else:
            obj = obj[token]
    return obj

def leaves(obj, prefix=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from leaves(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from leaves(value, f"{prefix}[{index}]")
    else:
        yield prefix, obj


def required_edits(task):
    """Leaf fields that differ between the starting form and the reference solution."""
    start = dict(leaves(task["initial_config"]))
    fixed = dict(leaves(task["reference_solution"]))
    return sorted(path for path in start.keys() | fixed.keys()
                  if not same(start.get(path, None), fixed.get(path, None))
                  or (path in start) != (path in fixed))


def participant_text(task, block="participant"):
    """The technical wording the LLM receives, or (block="display") the plain wording the page shows."""
    p = task[block]
    parts = [p["title"], p["scenario"], p.get("notes", "")] + [r["text"] for r in p["requirements"]]
    return " ".join(parts)


def fingerprint(task, rules):
    scored = task["scoring"]["rules"]
    traps = task["design"]["traps"]
    return {
        "resource": task["resource"],
        "rules": sorted(scored),
        "severities": sorted(Counter(rules[r]["severity"] for r in scored).items()),
        "max_weight": sum(WEIGHTS[rules[r]["severity"]] for r in scored),
        "trap_rules": sorted(t["rule"] for t in traps),
        "trap_paths": sorted(t["path"] for t in traps),
        "trap_weight": sum(WEIGHTS[rules[t["rule"]]["severity"]] for t in traps),
        "decision_paths": sorted(d["path"] for d in task["design"]["decision_points"]),
        "edits": required_edits(task),
        "requirements": len(task["participant"]["requirements"]),
    }


# ---------------------------------------------------------------- checks

def check_catalogue(catalogue, problems):
    if catalogue.get("severity_weights") != WEIGHTS:
        problems.append(f"catalogue: severity_weights must be {WEIGHTS}")
    categories = set(catalogue.get("categories", {}))
    rules = {}
    for rule in catalogue["rules"]:
        rid = rule.get("id", "?")
        where = f"catalogue {rid}"
        if rid in rules:
            problems.append(f"{where}: duplicate rule id")
        missing = RULE_KEYS - rule.keys()
        if missing:
            problems.append(f"{where}: missing keys {sorted(missing)}")
            continue
        if RESOURCE_PREFIX.get(rule["resource"]) != rid[:2]:
            problems.append(f"{where}: id prefix does not match resource {rule['resource']}")
        if rule["severity"] not in WEIGHTS:
            problems.append(f"{where}: unknown severity {rule['severity']}")
        if rule["category"] not in categories:
            problems.append(f"{where}: unknown category {rule['category']}")
        if not rule["security_hub"] and rule["severity_source"] != "researcher-assigned":
            problems.append(f"{where}: no Security Hub control, so severity_source must be 'researcher-assigned'")
        rules[rid] = rule
    return rules


def check_task(task, rules, problems):
    tid = task.get("task_id", "?")
    where = f"task {tid}"
    missing = TASK_KEYS - task.keys()
    if missing:
        problems.append(f"{where}: missing keys {sorted(missing)}")
        return

    resource = task["resource"]
    start, fixed = task["initial_config"], task["reference_solution"]
    if resource not in RESOURCE_PREFIX:
        problems.append(f"{where}: unknown resource {resource}")
    for name, config in (("initial_config", start), ("reference_solution", fixed)):
        if config.get("resource") != resource:
            problems.append(f"{where}: {name}.resource is not {resource}")
        for problem in config_problems(config):
            problems.append(f"{where}: {name} does not match the schema: {problem}")

    # Same form shape before and after (except where the task asks for a new row).
    if task["scored"] and {p for p, _ in leaves(start)} != {p for p, _ in leaves(fixed)}:
        problems.append(f"{where}: initial_config and reference_solution have different fields")

    scoring, design = task["scoring"], task["design"]
    req_ids = [r["id"] for r in task["participant"]["requirements"]]

    if not task["scored"]:
        if scoring["rules"] or design["traps"]:
            problems.append(f"{where}: unscored task must have no rules and no traps")
    else:
        if not 1 <= len(design["traps"]) <= MAX_TRAPS:
            problems.append(f"{where}: needs 1 to {MAX_TRAPS} traps, has {len(design['traps'])}")

        needed_params = set()
        for rid in scoring["rules"]:
            rule = rules.get(rid)
            if rule is None:
                problems.append(f"{where}: rule {rid} is not in the catalogue")
                continue
            if rule["resource"] != resource:
                problems.append(f"{where}: rule {rid} is for {rule['resource']}, not {resource}")
            needed_params.update(rule["params"])
        given_params = set(scoring["params"])
        if needed_params != given_params:
            problems.append(f"{where}: params {sorted(given_params)} do not match "
                            f"what the rules need {sorted(needed_params)}")

        # Every requirement is scored, every rule belongs to exactly one requirement.
        mapped = Counter(r for rids in scoring["requirement_map"].values() for r in rids)
        if sorted(scoring["requirement_map"]) != sorted(req_ids):
            problems.append(f"{where}: requirement_map keys {sorted(scoring['requirement_map'])} "
                            f"do not match requirements {req_ids}")
        for rid in scoring["rules"]:
            if mapped[rid] != 1:
                problems.append(f"{where}: rule {rid} is mapped to {mapped[rid]} requirements (need 1)")
        for rid in mapped:
            if rid not in scoring["rules"]:
                problems.append(f"{where}: requirement_map uses {rid}, which is not scored")

    def value_at(config, path, label):
        try:
            return get_path(config, path)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            problems.append(f"{where}: {label} path '{path}' not found ({exc})")
            return MISSING

    for trap in design["traps"]:
        label = f"trap {trap['id']}"
        if trap["rule"] not in scoring["rules"]:
            problems.append(f"{where}: {label} breaks {trap['rule']}, which is not scored")
        before = value_at(start, trap["path"], label)
        after = value_at(fixed, trap["path"], label)
        if before is not MISSING and not same(before, trap["value"]):
            problems.append(f"{where}: {label} value {trap['value']!r} is not what the form "
                            f"starts with ({before!r})")
        if after is not MISSING and same(after, trap["value"]):
            problems.append(f"{where}: {label} is not fixed in reference_solution")

    for point in design["decision_points"]:
        label = f"decision point {point['path']}"
        if point["rule"] not in scoring["rules"]:
            problems.append(f"{where}: {label} rule {point['rule']} is not scored")
        before = value_at(start, point["path"], label)
        after = value_at(fixed, point["path"], label)
        if before not in (None, "", MISSING):
            problems.append(f"{where}: {label} must start empty, starts as {before!r}")
        if after in (None, ""):
            problems.append(f"{where}: {label} is left empty in reference_solution")

    for default in design["secure_defaults"]:
        if default["rule"] not in scoring["rules"]:
            problems.append(f"{where}: secure default rule {default['rule']} is not scored")
        if default["path"]:
            label = f"secure default {default['path']}"
            before = value_at(start, default["path"], label)
            after = value_at(fixed, default["path"], label)
            if before is not MISSING and after is not MISSING and not same(before, after):
                problems.append(f"{where}: {label} changes in reference_solution")

    if task["scored"]:
        for field in design["unscored_fields"]:
            if field not in start:
                problems.append(f"{where}: unscored field {field} is not in initial_config")
            elif not same(start[field], fixed.get(field)):
                problems.append(f"{where}: unscored field {field} changes in reference_solution")

    # Neither wording (technical for the LLM, plain for the page) may give the answer key away.
    for block in ("participant", "display"):
        text = participant_text(task, block)
        if RULE_ID.search(text):
            problems.append(f"{where}: {block} text mentions a rule id")
        if re.search(r"\btraps?\b", text, re.IGNORECASE):
            problems.append(f"{where}: {block} text mentions traps")
        for trap in design["traps"]:
            if isinstance(trap["value"], str) and len(trap["value"]) > 3 and trap["value"] in text:
                problems.append(f"{where}: {block} text contains trap value {trap['value']!r}")

    # The plain wording must ask for the same things: same requirements, same addresses and names.
    if [r["id"] for r in task["display"]["requirements"]] != req_ids:
        problems.append(f"{where}: display requirements do not match participant requirements {req_ids}")
    plain = participant_text(task, "display")
    for name, value in scoring["params"].items():
        if str(value).split("/")[0] not in plain:
            problems.append(f"{where}: display text does not give {name} ({value})")


def check_pairs(tasks, rules, problems):
    pairs = {}
    for task in tasks.values():
        if task["scored"]:
            pairs.setdefault(task["pair"], {})[task["set"]] = task
    for pair, members in sorted(pairs.items()):
        if sorted(members) != ["A", "B"]:
            problems.append(f"pair {pair}: needs exactly one Set A and one Set B task, has {sorted(members)}")
            continue
        a, b = members["A"], members["B"]
        fa, fb = fingerprint(a, rules), fingerprint(b, rules)
        for key in fa:
            if fa[key] != fb[key]:
                problems.append(f"pair {pair}: {a['task_id']} and {b['task_id']} differ in {key}: "
                                f"{fa[key]} vs {fb[key]}")
        if a["order_in_set"] != b["order_in_set"]:
            problems.append(f"pair {pair}: tasks are in different positions in their sets")
        for block in ("participant", "display"):
            wa, wb = len(participant_text(a, block).split()), len(participant_text(b, block).split())
            if min(wa, wb) / max(wa, wb) < MIN_WORD_RATIO:
                problems.append(f"pair {pair}: {block} text lengths {wa} and {wb} words differ by more than 15%")


def check_sets(sets, tasks, problems):
    for name in ("A", "B"):
        listed = sets["sets"].get(name, [])
        actual = sorted((t for t in tasks.values() if t["scored"] and t["set"] == name),
                        key=lambda t: t["order_in_set"])
        if listed != [t["task_id"] for t in actual]:
            problems.append(f"sets.json: Set {name} should be {[t['task_id'] for t in actual]} "
                            f"(in order), is {listed}")
    a_pairs = [tasks[t]["pair"] for t in sets["sets"]["A"] if t in tasks]
    b_pairs = [tasks[t]["pair"] for t in sets["sets"]["B"] if t in tasks]
    if a_pairs != b_pairs:
        problems.append(f"sets.json: Set A and Set B are not in matching pair order ({a_pairs} vs {b_pairs})")
    practice = tasks.get(sets.get("practice"))
    if practice is None or practice["scored"]:
        problems.append("sets.json: practice task is missing or scored")
    groups = sets["counterbalancing_groups"]
    if len(groups) != 4:
        problems.append(f"sets.json: expected 4 counterbalancing groups, found {len(groups)}")
    for gid, sessions in groups.items():
        if sorted(s["set"] for s in sessions) != ["A", "B"] or \
           sorted(s["condition"] for s in sessions) != ["assisted", "unassisted"]:
            problems.append(f"sets.json: group {gid} must do Set A and Set B, once in each condition")
    combos = {tuple((s["set"], s["condition"]) for s in sessions) for sessions in groups.values()}
    if len(combos) != len(groups):
        problems.append("sets.json: two counterbalancing groups have the same schedule")


# ---------------------------------------------------------------- task sheet

def md(text):
    return str(text).replace("|", "\\|")


def show(value):
    return "(empty)" if value in (None, "") else json.dumps(value)


def cis_cell(rule, version):
    numbers = (rule["cis"] or {}).get(version)
    return ", ".join(numbers) if numbers else "-"


def write_sheet(catalogue, rules, tasks, sets):
    out = ["# Task sheet",
           "",
           "> Generated by `python tasks/check_tasks.py --write-sheet` from the JSON files in this",
           "> folder. Do not edit by hand: change the JSON and run the script again.",
           "",
           "## 1. Pair equivalence",
           "",
           "A and B tasks in a pair must match on every column (enforced by the checker).",
           "",
           "| Task | Set | Resource | Rules | Max weight | Traps (weight) | Decision points | Edits to a secure config | Words |",
           "|---|---|---|---|---|---|---|---|---|"]
    for set_name in ("A", "B"):
        for tid in sets["sets"][set_name]:
            task = tasks[tid]
            f = fingerprint(task, rules)
            out.append(f"| {tid} | {set_name} | {task['resource']} | {len(f['rules'])} | {f['max_weight']} | "
                       f"{len(f['trap_rules'])} ({f['trap_weight']}) | {len(f['decision_paths'])} | "
                       f"{len(f['edits'])} | {len(participant_text(task).split())} |")

    out += ["",
            "## 2. Rule catalogue",
            "",
            catalogue["severity_policy"],
            "",
            "| ID | Rule | Category | Severity (weight) | Fails when | CIS v5.0.0 | CIS v3.0.0 | Security Hub control | Severity source |",
            "|---|---|---|---|---|---|---|---|---|"]
    for rule in catalogue["rules"]:
        hub = ", ".join(f"{c['id']} ({c['severity']})" for c in rule["security_hub"]) or "-"
        fails = rule["fails_when"] + (f" *{rule['precedence']}*" if rule["precedence"] else "")
        out.append(f"| {rule['id']} | {md(rule['title'])} | {rule['category']} | "
                   f"{rule['severity']} ({WEIGHTS[rule['severity']]}) | {md(fails)} | "
                   f"{cis_cell(rule, 'v5.0.0')} | {cis_cell(rule, 'v3.0.0')} | {hub} | "
                   f"{md(rule['severity_source'])} |")
    out += ["", "Scoring conventions:", ""]
    out += [f"- {line}" for line in catalogue["scoring_conventions"]]

    out += ["", "## 3. Tasks", ""]
    order = [sets["practice"]] + sets["sets"]["A"] + sets["sets"]["B"]
    for tid in order:
        task = tasks[tid]
        heading = "practice, not scored" if not task["scored"] else \
            f"Set {task['set']}, pair {task['pair']}, task {task['order_in_set']} of the set"
        out += [f"### {tid}: {md(task['display']['title'])}",
                "",
                f"*{heading} | resource: `{task['resource']}`*",
                ""]
        for block, label in (("display", "What the participant sees (plain wording)"),
                             ("participant", "What the LLM receives as the task requirements (exact wording, never displayed)")):
            p = task[block]
            out += [f"**{label}**", "", f"> {p['scenario']}", ">"]
            out += [f"> {i}. {r['text']}" for i, r in enumerate(p["requirements"], 1)]
            if p.get("notes"):
                out += [">", f"> *{p['notes']}*"]
            out += [""]

        if task["scored"]:
            scoring, design = task["scoring"], task["design"]
            out += ["**Researcher only (never shown to participants or sent to the LLM)**", ""]
            out.append("- Requirement to rule: " + "; ".join(
                f"{req} -> {', '.join(rids)}" for req, rids in scoring["requirement_map"].items()))
            out.append("- Parameters: " + ", ".join(
                f"`{k}` = `{v}`" for k, v in scoring["params"].items()))
            out.append(f"- Maximum weighted score: {fingerprint(task, rules)['max_weight']}")
            out += ["", "| Trap | Field | Pre-filled value | Rule broken | Why it is realistic |",
                    "|---|---|---|---|---|"]
            for trap in design["traps"]:
                out.append(f"| {trap['id']} | `{trap['path']}` | `{md(show(trap['value']))}` | "
                           f"{trap['rule']} | {md(trap['basis'])} |")
            if design["decision_points"]:
                out += ["", "Decision points (start empty; the participant must choose):", ""]
                for point in design["decision_points"]:
                    options = point["options"]
                    options = ", ".join(options) if isinstance(options, list) else options
                    out.append(f"- `{point['path']}` (scored by {point['rule']}; options: {options})")
            if design["secure_defaults"]:
                out += ["", "Secure defaults (already correct; must not be broken):", ""]
                for default in design["secure_defaults"]:
                    field = f"`{default['path']}`" if default["path"] else default.get("note", "")
                    out.append(f"- {field} (scored by {default['rule']})")
            start = dict(leaves(task["initial_config"]))
            fixed = dict(leaves(task["reference_solution"]))
            out += ["", "Changes that turn the starting form into a fully secure answer:", ""]
            for path in fingerprint(task, rules)["edits"]:
                out.append(f"- `{path}`: {show(start.get(path))} -> {show(fixed.get(path))}")
            out.append("")
        out += ["Starting form (`initial_config`):", "", "```json",
                json.dumps(task["initial_config"], indent=2), "```", ""]
    SHEET.write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-sheet", action="store_true", help="also write tasks/TASKS.md")
    args = parser.parse_args()

    catalogue = json.loads((HERE / "rules_catalogue.json").read_text(encoding="utf-8-sig"))
    sets = json.loads((HERE / "sets.json").read_text(encoding="utf-8-sig"))
    tasks = {}
    for path in sorted(HERE.glob("task_*.json")):
        task = json.loads(path.read_text(encoding="utf-8-sig"))
        if path.stem != f"task_{task.get('task_id')}":
            print(f"{path.name}: task_id {task.get('task_id')!r} does not match the file name")
            return 1
        tasks[task["task_id"]] = task

    problems = []
    rules = check_catalogue(catalogue, problems)
    for task in tasks.values():
        check_task(task, rules, problems)
    if not problems:
        check_pairs(tasks, rules, problems)
        check_sets(sets, tasks, problems)

    scored = [t for t in tasks.values() if t["scored"]]
    print(f"{len(rules)} rules, {len(scored)} scored tasks, {len(tasks) - len(scored)} practice task(s)")
    if not problems:
        print(f"{'Task':<5} {'Resource':<15} {'Rules':>5} {'MaxW':>5} {'Traps':>5} {'TrapW':>5} "
              f"{'Decide':>6} {'Edits':>5} {'Words':>5}")
        for set_name in ("A", "B"):
            for tid in sets["sets"][set_name]:
                task = tasks[tid]
                f = fingerprint(task, rules)
                print(f"{tid:<5} {task['resource']:<15} {len(f['rules']):>5} {f['max_weight']:>5} "
                      f"{len(f['trap_rules']):>5} {f['trap_weight']:>5} {len(f['decision_paths']):>6} "
                      f"{len(f['edits']):>5} {len(participant_text(task).split()):>5}")

    if problems:
        print(f"\n{len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nAll checks passed.")
    if args.write_sheet:
        write_sheet(catalogue, rules, tasks, sets)
        print(f"Wrote {SHEET.relative_to(HERE.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
