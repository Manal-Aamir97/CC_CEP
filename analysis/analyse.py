"""Analyse the study's event logs (build plan Step 10).

    python -m analysis.analyse                         # logs/ -> analysis/results/
    python -m analysis.analyse --logs <folder> --out <folder>

Writes summary.md (every table for Section 4), per_task.csv, per_participant.csv,
advice_review.csv (assisted tasks with errors, for the manual "advice-induced error" check) and
figures/*.svg. Main test (H1): each participant's errors with the assistant against without it,
paired; both the paired t-test and the Wilcoxon signed-rank test are reported (the plan prefers
Wilcoxon if the differences are not normal, which is likely with 20 to 30 people).
Differences are always "with LLM minus without LLM": negative = fewer errors with the assistant.
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import quantiles

from analysis import stats
from app.events import read_events
from validator import ConfigError, submission_problems
from validator.validator import catalogue_rules

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = ("unassisted", "assisted")
LABEL = {"unassisted": "Without LLM", "assisted": "With LLM"}
CATEGORY_LABEL = {"network": "Network access", "public_access": "Public access", "encryption": "Encryption",
                  "backup_recovery": "Backups and recovery", "logging": "Logging",
                  "functional": "Website reachable (functional)"}
SYNTHETIC_MARKER = "SYNTHETIC_DATA.txt"


# ---------------------------------------------------------------- extraction

def ready(config):
    try:
        return isinstance(config, dict) and not submission_problems(config)
    except ConfigError:
        return False


def corrections(task_events, condition):
    """Build plan: field changes after the first LLM answer (assisted) or after the first full draft."""
    changes = [e for e in task_events if e["event"] == "field_change"]
    if condition == "assisted":
        answers = [e for e in task_events if e["event"] == "assistant_answer"]
        return sum(1 for c in changes if c["at"] > answers[0]["at"]) if answers else None
    start = next((e for e in task_events if e["event"] == "task_start"), None)
    if start is not None and ready(start.get("initial_config")):
        return len(changes)
    for i, change in enumerate(changes):
        if ready(change.get("config")):
            return len(changes) - i - 1
    return 0


def extract(events):
    people, by_task = {}, defaultdict(list)
    for e in events:
        pid = e["participant_id"]
        person = people.setdefault(pid, {"participant_id": pid, "group": e["group"], "experience": None,
                                         "year_of_study": None, "post": None})
        if e["event"] == "background":
            person["experience"], person["year_of_study"] = e["cloud_experience"], e["year_of_study"]
        elif e["event"] == "post_questionnaire":
            person["post"] = {k: e[k] for k in ("helpfulness", "trust", "confidence", "comments")}
        if e.get("task_id"):
            by_task[(pid, e["task_id"])].append(e)

    rows = []
    for (pid, task_id), task_events in by_task.items():
        task_events.sort(key=lambda e: e["at"])
        submit = next((e for e in task_events if e["event"] == "task_submit"), None)
        if submit is None or not submit.get("scored"):
            continue
        result = submit["result"]
        rows.append({
            "participant_id": pid, "group": people[pid]["group"], "experience": people[pid]["experience"],
            "task_id": task_id, "set": submit["set"], "part": submit["part"], "condition": submit["condition"],
            "error_count": result["error_count"], "weighted_score": result["weighted_score"],
            "max_weighted_score": result["max_weighted_score"], "task_success": result["task_success"],
            "duration_s": submit.get("duration_s"),
            "questions": sum(1 for e in task_events if e["event"] == "assistant_question"),
            "field_changes": sum(1 for e in task_events if e["event"] == "field_change"),
            "corrections": corrections(task_events, submit["condition"]),
            "failed_rules": result["failed_rules"], "passed_rules": result["passed_rules"],
            "errors_by_category": result["errors_by_category"],
            "answers": [e["answer"] for e in task_events if e["event"] == "assistant_answer"],
        })
    rows.sort(key=lambda r: (r["participant_id"], r["part"], r["task_id"]))
    return people, rows


def participant_table(people, rows):
    table = []
    for pid, person in sorted(people.items()):
        mine = [r for r in rows if r["participant_id"] == pid]
        row = {"participant_id": pid, "group": person["group"], "experience": person["experience"]}
        for cond in CONDITIONS:
            these = [r for r in mine if r["condition"] == cond]
            row[f"tasks_{cond}"] = len(these)
            row[f"errors_{cond}"] = sum(r["error_count"] for r in these)
            row[f"weighted_{cond}"] = sum(r["weighted_score"] for r in these)
            row[f"successes_{cond}"] = sum(1 for r in these if r["task_success"])
            row[f"time_{cond}"] = round(sum(r["duration_s"] or 0 for r in these), 1)
            corr = [r["corrections"] for r in these if r["corrections"] is not None]
            row[f"corrections_{cond}"] = sum(corr) if corr else None
        row["questions_assisted"] = sum(r["questions"] for r in mine if r["condition"] == "assisted")
        for key, value in (("set", "A"), ("set", "B"), ("part", 1), ("part", 2)):
            row[f"errors_{key}{value}"] = sum(r["error_count"] for r in mine if r[key] == value)
        row["complete"] = row["tasks_unassisted"] == 3 and row["tasks_assisted"] == 3
        if person["post"]:
            row.update({f"post_{k}": v for k, v in person["post"].items() if k != "comments"})
        table.append(row)
    return table


# ---------------------------------------------------------------- formatting

def fmt(value, digits=2):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def p_text(p):
    """'p = 0.034' or 'p < 0.001'."""
    return "p = -" if p is None else ("p < 0.001" if p < 0.001 else f"p = {p:.3f}")


def describe(values):
    return f"{stats.mean(values):.2f} ({stats.stdev(values):.2f})" if len(values) > 1 else (
        f"{values[0]:.2f}" if values else "-")


def comparison_row(name, a, b):
    """a = without LLM, b = with LLM (paired lists)."""
    diffs = [y - x for x, y in zip(a, b)]
    t = stats.paired_t(diffs)
    w = stats.wilcoxon(diffs)
    ci = t.get("ci95")
    t_part = f"t({t['df']}) = {t['t']:.2f}, {p_text(t['p'])}" if "t" in t else t.get("error", "-")
    method = "exact" if w["method"] == "exact" else ("normal approx." if w["method"].startswith("normal") else w["method"])
    return (f"| {name} | {describe(a)} | {describe(b)} | "
            f"{fmt(stats.mean(diffs)) if diffs else '-'}{f' [{ci[0]:.2f}, {ci[1]:.2f}]' if ci else ''} | "
            f"{t_part} | W = {fmt(float(w['W']), 1)}, {p_text(w['p'])} ({method}) | "
            f"d_z = {fmt(t.get('cohen_dz'))}; r = {fmt(w['rank_biserial'])} |")


# ---------------------------------------------------------------- figures (hand-written SVG)

def svg_paired(table, out):
    complete = [r for r in table if r["complete"]]
    if not complete:
        return
    a = [r["errors_unassisted"] for r in complete]
    b = [r["errors_assisted"] for r in complete]
    top_value = max(a + b + [1])
    x_pos = {"unassisted": 200, "assisted": 440}
    y = lambda v: 330 - v / top_value * 250
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 380" font-family="Segoe UI, Arial" font-size="13">',
             '<rect width="640" height="380" fill="white"/>',
             f'<text x="24" y="30" font-size="16" font-weight="600">Errors per participant, without and with the assistant (n = {len(complete)})</text>',
             '<text x="24" y="50" fill="#555">Each grey line is one participant; boxes show median and quartiles.</text>']
    for v in range(0, top_value + 1, max(1, top_value // 6)):
        parts.append(f'<line x1="80" x2="560" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#eee"/>'
                     f'<text x="70" y="{y(v) + 4:.1f}" text-anchor="end" fill="#555">{v}</text>')
    for r in complete:
        parts.append(f'<line x1="{x_pos["unassisted"]}" y1="{y(r["errors_unassisted"]):.1f}" x2="{x_pos["assisted"]}" '
                     f'y2="{y(r["errors_assisted"]):.1f}" stroke="#bbb" stroke-width="1"/>')
    for cond, values in (("unassisted", a), ("assisted", b)):
        x = x_pos[cond]
        q1, q2, q3 = quantiles(values, n=4, method="inclusive") if len(values) > 1 else (values[0],) * 3
        colour = "#1f6feb" if cond == "assisted" else "#6b7280"
        parts.append(f'<rect x="{x - 30}" y="{y(q3):.1f}" width="60" height="{max(1, y(q1) - y(q3)):.1f}" '
                     f'fill="{colour}" fill-opacity="0.15" stroke="{colour}"/>'
                     f'<line x1="{x - 30}" x2="{x + 30}" y1="{y(q2):.1f}" y2="{y(q2):.1f}" stroke="{colour}" stroke-width="3"/>')
        for v in values:
            parts.append(f'<circle cx="{x}" cy="{y(v):.1f}" r="4" fill="{colour}" fill-opacity="0.6"/>')
        parts.append(f'<text x="{x}" y="358" text-anchor="middle" font-weight="600">{LABEL[cond]}</text>')
    parts.append('<text x="24" y="190" transform="rotate(-90 24 190)" text-anchor="middle" fill="#555">Failed rules (3 tasks)</text></svg>')
    out.write_text("\n".join(parts), encoding="utf-8")


def svg_categories(category_totals, out):
    cats = sorted({c for cond in CONDITIONS for c in category_totals[cond]},
                  key=lambda c: -(category_totals["unassisted"][c] + category_totals["assisted"][c]))
    if not cats:
        return
    top_value = max(max(category_totals[c].values(), default=0) for c in CONDITIONS) or 1
    height = 90 + len(cats) * 46
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 {height}" font-family="Segoe UI, Arial" font-size="13">',
             f'<rect width="640" height="{height}" fill="white"/>',
             '<text x="24" y="30" font-size="16" font-weight="600">Failed rules by category, without and with the assistant</text>',
             '<rect x="24" y="44" width="12" height="12" fill="#6b7280"/><text x="42" y="54">Without LLM</text>'
             '<rect x="140" y="44" width="12" height="12" fill="#1f6feb"/><text x="158" y="54">With LLM</text>']
    for i, cat in enumerate(cats):
        y0 = 80 + i * 46
        parts.append(f'<text x="200" y="{y0 + 18}" text-anchor="end">{CATEGORY_LABEL.get(cat, cat)}</text>')
        for j, cond in enumerate(CONDITIONS):
            value = category_totals[cond].get(cat, 0)
            width = value / top_value * 360
            colour = "#1f6feb" if cond == "assisted" else "#6b7280"
            parts.append(f'<rect x="210" y="{y0 + j * 16}" width="{width:.1f}" height="14" fill="{colour}"/>'
                         f'<text x="{215 + width:.1f}" y="{y0 + j * 16 + 11}" fill="#333">{value}</text>')
    parts.append("</svg>")
    out.write_text("\n".join(parts), encoding="utf-8")


# ---------------------------------------------------------------- report

def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (";".join(v) if isinstance(v, list) else json.dumps(v) if isinstance(v, dict) else v)
                             for k, v in row.items()})


def analyse(log_dir, out_dir):
    log_dir, out_dir = Path(log_dir), Path(out_dir)
    events = read_events(log_dir)
    if not events:
        raise SystemExit(f"No log files in {log_dir}")
    people, rows = extract(events)
    table = participant_table(people, rows)
    complete = [r for r in table if r["complete"]]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "figures").mkdir(exist_ok=True)
    synthetic = (log_dir / SYNTHETIC_MARKER).exists()
    rules = catalogue_rules()

    md = ["# Study results", "", "> Generated by `python -m analysis.analyse`. Do not edit by hand.", ""]
    if synthetic:
        md += ["> **SYNTHETIC DATA.** These logs were made by `analysis/make_synthetic_logs.py` to test the "
               "analysis. They are not study results and must never be reported.", ""]
    md += [f"Participants with logs: {len(table)}; complete (3 tasks in each condition): {len(complete)}. "
           f"Scored task submissions: {len(rows)}. Differences are *with LLM minus without LLM*: negative means "
           "fewer with the assistant.", ""]

    md += ["## Main comparison (per participant, 3 tasks per condition)", "",
           "| Measure | Without LLM, mean (SD) | With LLM, mean (SD) | Mean difference [95% CI] | Paired t-test | Wilcoxon signed-rank | Effect sizes |",
           "|---|---|---|---|---|---|---|"]
    for name, key in (("Error count", "errors"), ("Weighted error score", "weighted"),
                      ("Tasks without a serious error (of 3)", "successes"), ("Time (s)", "time")):
        md.append(comparison_row(name, [r[f"{key}_unassisted"] for r in complete],
                                 [r[f"{key}_assisted"] for r in complete]))
    with_corr = [r for r in complete if r["corrections_unassisted"] is not None and r["corrections_assisted"] is not None]
    if len(with_corr) >= 2:
        md.append(comparison_row(f"Corrections (n = {len(with_corr)})", [r["corrections_unassisted"] for r in with_corr],
                                 [r["corrections_assisted"] for r in with_corr]))
    md += ["", "Wilcoxon p-values are exact when no differences are tied; with ties (usual for counts) the normal "
           "approximation with tie and continuity correction is used. Effect sizes: d_z = mean difference / SD of "
           "differences; r = matched-pairs rank-biserial correlation.", "",
           "Corrections: field changes after the first assistant answer (with LLM) or after the first complete "
           "draft (without LLM), as defined in the build plan. Participants who never asked are left out of that row.", ""]

    category_totals = {cond: Counter() for cond in CONDITIONS}
    for r in rows:
        category_totals[r["condition"]].update(r["errors_by_category"])
    md += ["## Errors by category", "", "| Category | Without LLM | With LLM |", "|---|---|---|"]
    for cat in sorted({c for cond in CONDITIONS for c in category_totals[cond]}):
        md.append(f"| {CATEGORY_LABEL.get(cat, cat)} | {category_totals['unassisted'][cat]} | {category_totals['assisted'][cat]} |")

    md += ["", "## Failure rate per rule", "",
           "| Rule | Mistake | Without LLM: failed / checked | With LLM: failed / checked |", "|---|---|---|---|"]
    for rid in rules:
        cells = []
        for cond in CONDITIONS:
            checked = [r for r in rows if r["condition"] == cond and (rid in r["failed_rules"] or rid in r["passed_rules"])]
            failed = sum(1 for r in checked if rid in r["failed_rules"])
            cells.append(f"{failed} / {len(checked)}" + (f" ({100 * failed / len(checked):.0f}%)" if checked else ""))
        md.append(f"| {rid} | {rules[rid]['title']} | {cells[0]} | {cells[1]} |")

    md += ["", "## Does prior cloud experience change the effect?", "",
           "| Group | n | Mean errors without LLM | Mean errors with LLM | Mean difference |", "|---|---|---|---|---|"]
    for name, test in (("Novices (experience 1-2)", lambda e: e is not None and e <= 2),
                       ("Experienced (3-5)", lambda e: e is not None and e >= 3)):
        sub = [r for r in complete if test(r["experience"])]
        if sub:
            a, b = [r["errors_unassisted"] for r in sub], [r["errors_assisted"] for r in sub]
            md.append(f"| {name} | {len(sub)} | {stats.mean(a):.2f} | {stats.mean(b):.2f} | {stats.mean([y - x for x, y in zip(a, b)]):.2f} |")
    rho = stats.spearman([r["experience"] for r in complete if r["experience"] is not None],
                         [r["errors_assisted"] - r["errors_unassisted"] for r in complete if r["experience"] is not None])
    md += ["", f"Spearman correlation between experience and the difference: {fmt(rho)}.", ""]

    md += ["## Design checks", "", "| Check | Mean A | Mean B | Wilcoxon |", "|---|---|---|---|"]
    for name, ka, kb in (("Set A vs Set B (errors)", "errors_setA", "errors_setB"),
                         ("Part 1 vs part 2 (order effect)", "errors_part1", "errors_part2")):
        a, b = [r[ka] for r in complete], [r[kb] for r in complete]
        if len(a) >= 2:
            w = stats.wilcoxon([y - x for x, y in zip(a, b)])
            md.append(f"| {name} | {stats.mean(a):.2f} | {stats.mean(b):.2f} | {p_text(w['p'])} |")

    assisted_rows = [r for r in rows if r["condition"] == "assisted"]
    asked = [r for r in complete if r["questions_assisted"] > 0]
    md += ["", "## Use of the assistant", "",
           f"- Questions per assisted task: mean {fmt(stats.mean([r['questions'] for r in assisted_rows]) if assisted_rows else None)}; "
           f"assisted tasks with at least one question: {sum(1 for r in assisted_rows if r['questions'])} of {len(assisted_rows)}.",
           f"- Participants who asked at least once: {len(asked)} of {len(complete)}."]
    if len(asked) >= 2:
        md += ["", "Secondary result (build plan): only participants who used the assistant.", "",
               "| Measure | Without LLM, mean (SD) | With LLM, mean (SD) | Mean difference [95% CI] | Paired t-test | Wilcoxon signed-rank | Effect sizes |",
               "|---|---|---|---|---|---|---|",
               comparison_row("Error count", [r["errors_unassisted"] for r in asked], [r["errors_assisted"] for r in asked])]

    posts = [p["post"] for p in people.values() if p["post"]]
    if posts:
        md += ["", "## Post-questionnaire (1 to 5)", "", "| Question | Mean | Median |", "|---|---|---|"]
        for key in ("helpfulness", "trust", "confidence"):
            values = [p[key] for p in posts]
            md.append(f"| {key} | {stats.mean(values):.2f} | {stats.median(values)} |")
        md.append(f"\nWritten comments: {sum(1 for p in posts if p['comments'])} (see the logs).")

    baseline_path = ROOT / "dataset" / "results" / "baseline.json"
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["rules"]
        md += ["", "## Participants without the assistant vs real engineers in 2022", "",
               "| Rule | Participants without LLM: failed / checked | Real AWS projects (Verdet et al.): resource checks failed |",
               "|---|---|---|"]
        for rid, b in baseline.items():
            checked = [r for r in rows if r["condition"] == "unassisted" and (rid in r["failed_rules"] or rid in r["passed_rules"])]
            failed = sum(1 for r in checked if rid in r["failed_rules"])
            mine = f"{failed} / {len(checked)}" + (f" ({100 * failed / len(checked):.0f}%)" if checked else "")
            md.append(f"| {rid} | {mine} | {100 * b['resource_failure_rate']:.1f}% of {b['resource_checks']} |")
        md += ["", "Not identical measures: our tasks plant traps and state requirements, and Checkov judged the "
               "real projects. Use this as context, not as a statistical comparison."]

    md += ["", "## Files", "", "- `per_task.csv`, `per_participant.csv`: the numbers above, row by row",
           "- `advice_review.csv`: assisted tasks with errors, with the assistant's answers, for the manual "
           "advice-induced-error check", "- `figures/errors_by_condition.svg`, `figures/category_by_condition.svg`"]
    (out_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    write_csv(out_dir / "per_task.csv", rows,
              ["participant_id", "group", "experience", "task_id", "set", "part", "condition", "error_count",
               "weighted_score", "max_weighted_score", "task_success", "duration_s", "questions", "field_changes",
               "corrections", "failed_rules", "errors_by_category"])
    write_csv(out_dir / "per_participant.csv", table, list(table[0].keys()) if table else [])
    review = [{"participant_id": r["participant_id"], "task_id": r["task_id"], "failed_rules": r["failed_rules"],
               "assistant_answers": " || ".join(r["answers"]), "advice_induced (yes/no/unclear)": "", "notes": ""}
              for r in assisted_rows if r["failed_rules"] and r["answers"]]
    write_csv(out_dir / "advice_review.csv", review,
              ["participant_id", "task_id", "failed_rules", "assistant_answers", "advice_induced (yes/no/unclear)", "notes"])
    svg_paired(table, out_dir / "figures" / "errors_by_condition.svg")
    svg_categories(category_totals, out_dir / "figures" / "category_by_condition.svg")
    print(f"{len(complete)} complete participants; wrote {out_dir / 'summary.md'}")
    return {"participants": len(table), "complete": len(complete), "rows": len(rows), "synthetic": synthetic}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Analyse the study's event logs.")
    parser.add_argument("--logs", default=str(ROOT / "logs"))
    parser.add_argument("--out", default=str(ROOT / "analysis" / "results"))
    args = parser.parse_args(argv)
    analyse(args.logs, args.out)


if __name__ == "__main__":
    main()
