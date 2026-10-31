"""Make one conversion worksheet per fetched project, to speed up the hand conversion.

    python -m dataset.worksheets      # writes dataset/results/worksheets/

A worksheet lists the Terraform resources relevant to the project's resource type, with file and
line numbers, plus the project's October 2022 Checkov failures. It quotes no code (the code stays
in dataset/raw/repos/), and it does not convert anything: converting is the researchers' job
(dataset/cases/README.md). Line ranges come from brace counting and are a guide only.
"""
import csv
import json
import re
from pathlib import Path

from dataset.baseline import RAW, RESULTS, read_results
from dataset.fetch_files import OUT as FETCHED

WORKSHEETS = RESULTS / "worksheets"
RELEVANT = {
    "web_server": ("aws_instance", "aws_launch_template", "aws_launch_configuration", "aws_security_group",
                   "aws_security_group_rule", "aws_vpc_security_group_ingress_rule", "aws_ebs_volume",
                   "aws_ebs_encryption_by_default"),
    "storage_bucket": ("aws_s3_bucket", "aws_s3_bucket_public_access_block", "aws_s3_bucket_versioning",
                       "aws_s3_bucket_logging", "aws_s3_bucket_server_side_encryption_configuration",
                       "aws_s3_bucket_acl", "aws_s3_bucket_policy"),
    "database": ("aws_db_instance", "aws_rds_cluster", "aws_rds_cluster_instance", "aws_security_group",
                 "aws_security_group_rule", "aws_vpc_security_group_ingress_rule"),
}
PRIMARY = {"web_server": "aws_instance", "storage_bucket": "aws_s3_bucket", "database": "aws_db_instance"}
BLOCK = re.compile(r'^\s*(resource|module)\s+"([^"]+)"(?:\s+"([^"]+)")?\s*\{')
# Test and example folders often hold deliberately insecure samples, not anyone's real configuration.
SAMPLE_PATH = re.compile(r"(^|/)(tests?|its|test[-_]?data|test[-_]?fixtures?|fixtures?|examples?|demos?|samples?)(/|$)", re.I)


def is_sample(path):
    return bool(SAMPLE_PATH.search(path))


def blocks(text):
    """(kind, type_or_name, name, first_line, last_line, var_refs) for top-level resource/module blocks."""
    lines = text.splitlines()
    found, i = [], 0
    while i < len(lines):
        m = BLOCK.match(lines[i])
        if not m:
            i += 1
            continue
        depth, j = 0, i
        while j < len(lines):
            code = re.sub(r'"(?:\\.|[^"\\])*"', '""', lines[j]).split("#")[0].split("//")[0]
            depth += code.count("{") - code.count("}")
            if depth <= 0 and j >= i:
                break
            j += 1
        body = "\n".join(lines[i:j + 1])
        found.append((m.group(1), m.group(2), m.group(3) or "", i + 1, j + 1, body.count("var.")))
        i = j + 1
    return found


def module_source(text, first, last):
    m = re.search(r'^\s*source\s*=\s*"([^"]+)"', "\n".join(text.splitlines()[first - 1:last]), re.M)
    return m.group(1) if m else "?"


def worksheet(entry, candidate, failed_2022):
    resource = candidate["resource"]
    folder = FETCHED / entry["repo"].replace("/", "__") / entry["commit"]
    rows, modules = [], []
    for f in entry["files"]:
        if "sha256" not in f:
            continue
        path = folder.joinpath(*f["path"].split("/"))
        text = path.read_text(encoding="utf-8", errors="replace")
        for kind, kind_type, name, first, last, var_refs in blocks(text):
            if kind == "resource" and kind_type in RELEVANT[resource]:
                rows.append((kind_type, name, f["path"], first, last, var_refs))
            elif kind == "module":
                modules.append((kind_type, f["path"], first, module_source(text, first, last)))
    rows.sort(key=lambda r: (is_sample(r[2]), r[2], r[3]))  # real configurations first
    primary = [r for r in rows if r[0] == PRIMARY[resource] and not is_sample(r[2])]
    samples = sum(1 for r in rows if is_sample(r[2]))
    out = [f"# Worksheet: {entry['repo']}", "",
           "| Item | Value |", "|---|---|",
           f"| Resource type for this case | {resource} |",
           f"| 2022 scan | {candidate['status']} ({candidate['role']}) |",
           f"| Checks it failed in 2022 | {', '.join(sorted(failed_2022)) or 'none of the mapped checks'} |",
           f"| Pinned commit | `{entry['commit']}` ({entry['commit_date']}) |",
           f"| Files fetched | {len(entry['files'])} `.tf` files{' (listing was cut short)' if entry.get('partial_listing') else ''} |",
           f"| Local folder | `dataset/raw/repos/{entry['repo'].replace('/', '__')}/{entry['commit']}/` |", "",
           f"## Relevant resources ({len(rows)})", ""]
    if rows:
        if samples:
            out += [f"{samples} of these are in test or example folders (marked below). They may be deliberately "
                    "insecure samples: do not convert them.", ""]
        out += ["| Type | Name | File | Lines | `var.` uses | Test or example folder |", "|---|---|---|---|---|---|"]
        out += [f"| `{t}` | `{n}` | `{p}` | {a}-{b} | {v} | {'yes' if is_sample(p) else ''} |" for t, n, p, a, b, v in rows]
    else:
        out.append("No matching resource blocks were found directly in this project's files.")
    if modules:
        out += ["", f"## Modules ({len(modules)})", "",
                "Resources inside modules are not listed above; check whether the module creates the resource.", "",
                "| Module | File | Line | Source |", "|---|---|---|---|"]
        out += [f"| `{n}` | `{p}` | {a} | `{s}` |" for n, p, a, s in modules]
    out += ["", "## Conversion checklist", "",
            f"- [ ] Pick one `{PRIMARY[resource]}` (suggested: "
            f"{'`' + primary[0][1] + '` in `' + primary[0][2] + '`' if primary else 'none outside test or example folders; see modules or choose a reserve'})",
            "- [ ] Collect the related resources (security groups, bucket settings) it uses",
            "- [ ] Convert following dataset/cases/README.md: copy values exactly, never guess, drop a rule instead",
            "- [ ] Save as dataset/cases/case_Cxx.json with full provenance; run `python -m dataset.check_cases`",
            "- [ ] Second researcher converts independently (at least 10 cases) and differences are settled"]
    return "\n".join(out) + "\n", len(rows), len(primary), len(modules)


def main():
    manifest_path = FETCHED / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with open(RESULTS / "case_candidates.csv", newline="", encoding="utf-8") as f:
        candidates = {row["repo"]: row for row in csv.DictReader(f)}
    projects, _ = read_results(RAW / "results.csv")
    failed = {p["repo"]: set(p["failed"]) for p in projects}
    WORKSHEETS.mkdir(parents=True, exist_ok=True)
    index = ["# Conversion worksheets", "", "> Generated by `python -m dataset.worksheets`. Do not edit by hand.", "",
             "| Project | Resource | 2022 scan | Fetch status | Relevant resources | Suggested pick found | Modules | Worksheet |",
             "|---|---|---|---|---|---|---|---|"]
    for key, entry in sorted(manifest.items(), key=lambda kv: (candidates.get(kv[1]["repo"], {}).get("resource", ""), kv[0])):
        repo = entry["repo"]
        candidate = candidates.get(repo)
        if candidate is None:
            continue
        if entry["status"] != "ok":
            index.append(f"| {repo} | {candidate['resource']} | {candidate['status']} | {entry['status']} | - | - | - | - |")
            continue
        text, n_rows, n_primary, n_modules = worksheet(entry, candidate, failed.get(repo, set()))
        name = repo.replace("/", "__") + ".md"
        (WORKSHEETS / name).write_text(text, encoding="utf-8")
        index.append(f"| {repo} | {candidate['resource']} | {candidate['status']} | ok | {n_rows} | "
                     f"{'yes' if n_primary else 'no'} | {n_modules} | [{name}]({name}) |")
    (WORKSHEETS / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"wrote {len(index) - 6} index rows to {WORKSHEETS / 'README.md'}")


if __name__ == "__main__":
    main()
