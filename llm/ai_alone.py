"""AI alone: the model completes each study task by itself, with no person involved.

    python -m llm.ai_alone run --provider stub                     # dry run: echoes the starting form
    python -m llm.ai_alone run --provider openrouter --runs 3
    python -m llm.ai_alone run --provider openrouter --resume llm/results/<run folder>
    python -m llm.ai_alone summarize --run llm/results/<run folder>

This is a reference point next to the study's two conditions, not one of them: it measures what the
fixed model produces when it fills in the form on its own. Report it as "AI alone", never as
AI-assisted participants.

The model receives what a participant's assistant may see (study.public_task: the exact technical task
text and the pre-filled starting form) plus the allowed fields and values from the schema, with every
description removed. It never receives scoring rules, traps, reference answers or validator findings.
It uses the study's fixed model and settings (client.SETTINGS), so repeated runs mainly show how stable
its answer is. Free models allow a limited number of requests a day: when the limit is reached the run
stops and keeps its results; --resume continues with the runs not yet answered.

Writes, in llm/results/ai_alone-<provider>-<time>/:
    answers.jsonl   task, run, prompt, model answer, model info, parsed configuration, validator result
    summary.md      errors per task, failure rate per rule, and how often the runs agree
"""
import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from app import study
from llm import client as llm_client
from llm.prompt import task_requirements
from validator import ConfigError, validate
from validator.schema import load_schema

RESULTS = Path(__file__).resolve().parent / "results"
PUBLIC_KEYS = set(study.PUBLIC_KEYS)
SYSTEM_PROMPT = ("You are configuring a cloud resource in a cloud console. Read the task, then return the "
                 "complete configuration you would submit. Reply with one JSON object only, with no explanation.")


class EchoStartingForm:
    """Dry-run client: answers with the task's starting form unchanged, so the pipeline can be checked."""
    provider = "stub"
    model = "echo-starting-form"
    configured = True

    def complete(self, prompt):
        text = prompt["messages"][-1]["content"]
        start = text.index("STARTING FORM:\n") + len("STARTING FORM:\n")
        form, _ = json.JSONDecoder().raw_decode(text[start:])
        return llm_client.Reply("```json\n" + json.dumps(form, indent=2) + "\n```", self.provider, self.model, 0)


def get_client(provider):
    return EchoStartingForm() if provider == "stub" else llm_client.get_client(provider)


def task_ids():
    s = study.sets()["sets"]
    return s["A"] + s["B"]


def _strip(node, defs):
    """A schema node with $ref resolved and every description or comment removed."""
    if isinstance(node, dict):
        if "$ref" in node:
            return _strip(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        return {k: _strip(v, defs) for k, v in node.items() if k not in ("description", "$comment")}
    if isinstance(node, list):
        return [_strip(v, defs) for v in node]
    return node


def allowed_values(resource):
    """The fields and values a configuration of this resource may have, without any guidance text."""
    defs = load_schema()["$defs"]
    return _strip(defs[resource], defs)


def build_prompt(public_task):
    extra = set(public_task) - PUBLIC_KEYS
    if extra:
        raise ValueError(f"prompt may only use public task data, got {sorted(extra)}")
    text = ("TASK:\n" + task_requirements(public_task)
            + "\n\nSTARTING FORM:\n" + json.dumps(public_task["initial_config"], indent=2)
            + "\n\nALLOWED FIELDS AND VALUES (JSON Schema):\n"
            + json.dumps(allowed_values(public_task["resource"]), indent=2)
            + "\n\nReturn the final configuration as one JSON object that uses only these fields and values.")
    return {"system": SYSTEM_PROMPT, "messages": [{"role": "user", "content": text}]}


def parse_config(answer):
    """The first JSON object in the answer (code fences allowed). Raises ValueError if there is none."""
    decoder = json.JSONDecoder()
    position = answer.find("{")
    while position != -1:
        try:
            value, _ = decoder.raw_decode(answer[position:])
        except ValueError:
            position = answer.find("{", position + 1)
            continue
        if isinstance(value, dict):
            return value
        position = answer.find("{", position + 1)
    raise ValueError("no JSON object in the answer")


def score_answer(task_id, answer):
    """(config or None, problem text or None, validator result dict or None)."""
    try:
        config = parse_config(answer)
    except ValueError as exc:
        return None, f"unreadable: {exc}", None
    try:
        result = validate(config, study.task(task_id))
    except ConfigError as exc:
        return config, "invalid: " + "; ".join(exc.problems), None
    return config, None, result.to_dict()


def answered(folder):
    path = folder / "answers.jsonl"
    if not path.exists():
        return set()
    with open(path, encoding="utf-8") as f:
        return {(r["task_id"], r["run"]) for r in (json.loads(line) for line in f if line.strip())}


def run(provider, runs, tasks=None, out_root=RESULTS, resume=None):
    client = get_client(provider)
    if client.provider == "none":
        raise SystemExit("No model connected: pass --provider (use 'stub' for a dry run).")
    if not client.configured:
        raise SystemExit("No API key: paste it into the .env file (OPENROUTER_API_KEY=...).")
    tasks = tasks or task_ids()
    unknown = set(tasks) - set(task_ids())
    if unknown:
        raise SystemExit(f"Unknown or unscored task: {', '.join(sorted(unknown))}")
    folder = Path(resume) if resume else out_root / f"ai_alone-{client.provider}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if resume and not (folder / "answers.jsonl").exists():
        raise SystemExit(f"Nothing to resume in {folder}.")
    folder.mkdir(parents=True, exist_ok=True)
    done = answered(folder)
    # Run 1 of every task first, then run 2, so a daily limit leaves every task with the same count.
    pending = [(t, r) for r in range(1, runs + 1) for t in tasks if (t, r) not in done]
    interval = getattr(client, "min_interval_s", 0)
    stopped = None
    with open(folder / "answers.jsonl", "a", encoding="utf-8") as out:
        for position, (task_id, number) in enumerate(pending):
            if position and interval:
                time.sleep(interval)
            prompt = build_prompt(study.public_task(task_id))
            try:
                reply = client.complete(prompt)
            except llm_client.RateLimited as exc:
                stopped = str(exc)
                break
            except llm_client.AssistantUnavailable as exc:
                print(f"failed: {task_id} run {number}: {exc}", flush=True)
                continue
            config, problem, result = score_answer(task_id, reply.text)
            out.write(json.dumps({
                "task_id": task_id, "set": study.task(task_id)["set"], "resource": study.task(task_id)["resource"],
                "run": number, "prompt": prompt, "answer": reply.text,
                "model": {"provider": reply.provider, "model": reply.model, "served_model": reply.served_model,
                          "finish_reason": reply.finish_reason, "usage": reply.usage, "latency_ms": reply.latency_ms},
                "settings": llm_client.SETTINGS, "config": config, "problem": problem, "result": result,
            }, ensure_ascii=False) + "\n")
            out.flush()
            status = problem or f"{result['error_count']} errors ({', '.join(result['failed_rules']) or 'none'})"
            print(f"{task_id} run {number}: {status}", flush=True)
    print(f"{len(answered(folder))} of {len(tasks) * runs} runs answered; wrote {folder}")
    if stopped:
        print(f"Stopped at the rate limit: {stopped}")
        print(f"To continue later: python -m llm.ai_alone run --provider {client.provider} --runs {runs} "
              f"--resume \"{folder}\"")
    summarize(folder)
    return folder


def _pct(part, whole):
    return f"{part} / {whole} ({100 * part / whole:.0f}%)" if whole else "-"


def summarize(folder):
    folder = Path(folder)
    with open(folder / "answers.jsonl", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    rules = {r["id"]: r for r in json.loads((study.ROOT / "tasks" / "rules_catalogue.json").read_text(encoding="utf-8-sig"))["rules"]}
    by_task = defaultdict(list)
    for r in records:
        by_task[r["task_id"]].append(r)
    models = sorted({r["model"].get("served_model") or r["model"]["model"] for r in records})

    lines = ["# AI alone: the model completes each task by itself", "",
             "> Generated by `python -m llm.ai_alone`. A reference point, not a study condition: no person was involved.", "",
             f"Model: {', '.join(models) or '-'}. Settings: {json.dumps(llm_client.SETTINGS)}. Answers: {len(records)}.", "",
             "## Per task", "",
             "| Task | Set | Resource | Answers | Usable | Mean errors | Mean weighted score (max) | Without a serious error | Distinct configurations | Failed rules (times) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    rule_checked, rule_failed = Counter(), Counter()
    usable_all = []
    for task_id in task_ids():
        rs = sorted(by_task.get(task_id, []), key=lambda r: r["run"])
        if not rs:
            continue
        ok = [r for r in rs if r["result"]]
        usable_all += ok
        for r in ok:
            rule_checked.update(r["result"]["failed_rules"] + r["result"]["passed_rules"])
            rule_failed.update(r["result"]["failed_rules"])
        failed = Counter(rule for r in ok for rule in r["result"]["failed_rules"])
        distinct = len({json.dumps(r["config"], sort_keys=True) for r in rs if r["config"] is not None})
        mean = lambda key: f"{sum(r['result'][key] for r in ok) / len(ok):.2f}" if ok else "-"
        top = ok[0]["result"]["max_weighted_score"] if ok else "-"
        lines.append(f"| {task_id} | {rs[0]['set']} | {rs[0]['resource']} | {len(rs)} | {len(ok)} | {mean('error_count')} | "
                     f"{mean('weighted_score')} ({top}) | {_pct(sum(r['result']['task_success'] for r in ok), len(ok))} | "
                     f"{distinct} | {', '.join(f'{k} ({v})' for k, v in sorted(failed.items())) or 'none'} |")

    unusable = [r for r in records if not r["result"]]
    lines += ["", f"Usable answers: {_pct(len(usable_all), len(records))}. "
                  "An answer is unusable when it holds no JSON object or the configuration breaks the schema."]
    if unusable:
        lines += ["", "| Task | Run | Problem |", "|---|---|---|"]
        lines += [f"| {r['task_id']} | {r['run']} | {r['problem'][:200]} |" for r in unusable]

    lines += ["", "## Failure rate per rule (usable answers)", "",
              "| Rule | Mistake | Severity | Failed / checked |", "|---|---|---|---|"]
    for rid in sorted(rule_checked):
        lines.append(f"| {rid} | {rules[rid]['title']} | {rules[rid]['severity']} | {_pct(rule_failed[rid], rule_checked[rid])} |")

    if usable_all:
        n = len(usable_all)
        lines += ["", "## Overall", "",
                  f"- Mean errors per task: {sum(r['result']['error_count'] for r in usable_all) / n:.2f}",
                  f"- Mean weighted score per task: {sum(r['result']['weighted_score'] for r in usable_all) / n:.2f}",
                  f"- Tasks without a serious error: {_pct(sum(r['result']['task_success'] for r in usable_all), n)}",
                  "", "Repeated runs use the same fixed settings (temperature 0, fixed seed), so they show how "
                      "stable the answer is rather than new independent samples."]
    (folder / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {folder / 'summary.md'}")
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description="AI alone: the model completes each task by itself.")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--provider", required=True, help="stub for a dry run, or openrouter")
    r.add_argument("--runs", type=int, default=3, help="answers per task (default 3)")
    r.add_argument("--tasks", help="comma-separated task IDs, default all six")
    r.add_argument("--resume", help="an earlier run folder to continue")
    s = sub.add_parser("summarize")
    s.add_argument("--run", required=True, help="a run folder under llm/results/")
    args = parser.parse_args(argv)
    llm_client.load_env_file()
    if args.command == "run":
        tasks = [t.strip() for t in args.tasks.split(",")] if args.tasks else None
        run(args.provider, args.runs, tasks=tasks, resume=args.resume)
    else:
        summarize(args.run)


if __name__ == "__main__":
    main()
