"""Choose which projects to fetch for the Step 7 offline test set.

    python -m dataset.select_cases            # writes dataset/results/case_candidates.csv

Per resource type (web server, storage bucket, database) it picks projects whose October 2022
scan failed at least one mapped check ("failing") and projects whose matching resources passed
every mapped check ("clean"), plus reserves in case a project is gone or cannot be converted.
The fixed seed makes the choice reproducible.
"""
import argparse
import csv
import json
import random

from dataset.baseline import MAPPING, RAW, RESULTS, read_results

RESOURCE_OF = {"WS": "web_server", "ST": "storage_bucket", "DB": "database"}
FIELDS = ["repo", "resource", "status", "role", "failed_checks"]


def resource_checks(mapping):
    checks = {resource: set() for resource in RESOURCE_OF.values()}
    for rule, entry in mapping["rules"].items():
        checks[RESOURCE_OF[rule[:2]]].update(entry["checks"])
    return checks


def classify(projects, mapping):
    """resource -> {'failing': [(repo, failed ids)], 'clean': [(repo, [])]}, projects sorted by name."""
    groups = {}
    for resource, checks in resource_checks(mapping).items():
        failing, clean = [], []
        for p in sorted(projects, key=lambda p: p["repo"]):
            seen = any(p["passed"][c] or p["failed"][c] for c in checks)
            failed = sorted(c for c in checks if p["failed"][c])
            if failed:
                failing.append((p["repo"], failed))
            elif seen:
                clean.append((p["repo"], []))
        groups[resource] = {"failing": failing, "clean": clean}
    return groups


def select(groups, n_failing=7, n_clean=3, n_reserve=5, seed=2026):
    rng = random.Random(seed)
    rows, used = [], set()
    for resource in sorted(groups):
        for status, wanted in (("failing", n_failing), ("clean", n_clean)):
            pool = [item for item in groups[resource][status] if item[0] not in used]
            picked = rng.sample(pool, min(len(pool), wanted + n_reserve))
            for index, (repo, failed) in enumerate(picked):
                used.add(repo)
                rows.append({"repo": repo, "resource": resource, "status": status,
                             "role": "main" if index < wanted else "reserve",
                             "failed_checks": ";".join(failed)})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--failing", type=int, default=7, help="failing projects per resource type")
    parser.add_argument("--clean", type=int, default=3, help="clean projects per resource type")
    parser.add_argument("--reserve", type=int, default=5, help="reserves per group")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args(argv)

    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    projects, _ = read_results(RAW / "results.csv")
    groups = classify(projects, mapping)
    rows = select(groups, args.failing, args.clean, args.reserve, args.seed)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "case_candidates.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    for resource, g in sorted(groups.items()):
        print(f"{resource}: {len(g['failing'])} failing and {len(g['clean'])} clean projects available")
    print(f"wrote {len(rows)} candidates to {out}")


if __name__ == "__main__":
    main()
