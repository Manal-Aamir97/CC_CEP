"""Offline check of the assistant (build plan Step 7): what does the model catch on its own?

    python -m llm.offline_check run --provider stub            # dry run with canned replies
    python -m llm.offline_check run --provider openrouter --source all
    python -m llm.offline_check run --provider openrouter --resume llm/results/<run folder>
    python -m llm.offline_check score --run llm/results/<run folder>

Free models allow a limited number of requests a day. When the limit is reached the run stops
and keeps its results; --resume continues with the cases not yet answered.

`run` asks the model about every case with the same fixed question the study's prompt format
uses, and writes, in llm/results/<provider>-<time>/:
    answers.jsonl   case, prompt, model answer, model info, the validator's hidden findings
    coding.csv      one row per (case, real error) for researchers to mark: did the advice catch
                    it? was it right? (+ one row per case for wrong or harmful advice)
    after/          an empty folder: put <case_id>.json here = the configuration after applying
                    exactly the model's advice (by hand, in the preview console)
`score` validates every after/<case_id>.json and reports errors before and after the advice.

The model never sees the findings: they are stored beside its answer, not sent to it.
"""
import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

from llm import client as llm_client
from llm.offline_cases import planted_cases, real_cases
from llm.prompt import build_messages
from validator import ConfigError, validate

RESULTS = Path(__file__).resolve().parent / "results"
QUESTION = ("Please review my configuration against the task requirements. "
            "What, if anything, should I change, and why?")
CODING_FIELDS = ["case_id", "row_type", "rule_id", "rule_title", "validator_message", "auto_hint",
                 "advice_mentions_it (yes/no)", "advice_is_correct (yes/partly/no)", "notes"]
HINTS = {  # words that suggest the advice touches the rule; a hint for the coder, never a verdict
    "WS-01": ("ssh", "22"), "WS-02": ("ssh", "22", "admin"), "WS-03": ("rdp", "3389"),
    "WS-04": ("http", "80"), "WS-05": ("https", "443"), "WS-06": ("encrypt",),
    "ST-01": ("public access", "block"), "ST-02": ("encrypt", "sse"), "ST-03": ("version",),
    "ST-04": ("log",), "DB-01": ("public",), "DB-02": ("0.0.0.0", "internet", "5432"),
    "DB-03": ("application server", "app server", "10.0."), "DB-04": ("encrypt",),
    "DB-05": ("backup", "retention"),
}


def select_cases(source):
    cases = []
    if source in ("planted", "all"):
        cases += planted_cases()
    if source in ("real", "all"):
        cases += real_cases()
    return cases


def answered_cases(folder):
    path = folder / "answers.jsonl"
    if not path.exists():
        return set()
    with open(path, encoding="utf-8") as f:
        return {json.loads(line)["case_id"] for line in f if line.strip()}


def run(provider, source, out_root=RESULTS, resume=None):
    client = llm_client.get_client(provider)
    if client.provider == "none":
        raise SystemExit("No model connected: pass --provider (use 'stub' for a dry run).")
    if not client.configured:
        raise SystemExit("No API key: paste it into the .env file (OPENROUTER_API_KEY=...).")
    cases = select_cases(source)
    if not cases:
        raise SystemExit(f"No cases for source {source!r}.")
    folder = Path(resume) if resume else out_root / f"{client.provider}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if resume and not (folder / "answers.jsonl").exists():
        raise SystemExit(f"Nothing to resume in {folder}.")
    (folder / "after").mkdir(parents=True, exist_ok=True)
    done = answered_cases(folder)
    pending = [c for c in cases if c["case_id"] not in done]
    interval = getattr(client, "min_interval_s", 0)
    failed, stopped = [], None
    with open(folder / "answers.jsonl", "a", encoding="utf-8") as answers, \
            open(folder / "coding.csv", "a", newline="", encoding="utf-8") as coding_file:
        coding = csv.DictWriter(coding_file, fieldnames=CODING_FIELDS)
        if not done:
            coding.writeheader()
        for position, case in enumerate(pending):
            if position and interval:
                time.sleep(interval)
            prompt = build_messages(case["task"], case["config"], QUESTION)
            try:
                reply = client.complete(prompt)
            except llm_client.RateLimited as exc:
                stopped = str(exc)
                break
            except llm_client.AssistantUnavailable as exc:
                failed.append(case["case_id"])
                print(f"failed: {case['case_id']}: {exc}", flush=True)
                continue
            answer = reply.text
            info = {"provider": reply.provider, "model": reply.model, "served_model": reply.served_model,
                    "finish_reason": reply.finish_reason, "usage": reply.usage, "latency_ms": reply.latency_ms}
            record = {"case_id": case["case_id"], "source": case["source"], "task_id": case["task_id"],
                      "question": QUESTION, "prompt": prompt, "answer": answer, "model": info,
                      "settings": llm_client.SETTINGS, "config": case["config"],
                      "expected_failures": case["expected_failures"], "findings": case["findings"]}
            if "provenance" in case:
                record["provenance"] = case["provenance"]
            answers.write(json.dumps(record, ensure_ascii=False) + "\n")
            lowered = answer.lower()
            for finding in case["findings"]:
                hint = "maybe" if any(word in lowered for word in HINTS.get(finding["rule_id"], ())) else ""
                coding.writerow({"case_id": case["case_id"], "row_type": "real error",
                                 "rule_id": finding["rule_id"], "rule_title": finding["title"],
                                 "validator_message": finding["message"], "auto_hint": hint})
            coding.writerow({"case_id": case["case_id"], "row_type": "wrong or harmful advice",
                             "rule_id": "", "rule_title": "Any advice that would break a requirement or add a problem",
                             "validator_message": "", "auto_hint": ""})
            answers.flush()
            coding_file.flush()
    finished = len(answered_cases(folder))
    print(f"{finished} of {len(cases)} cases answered; wrote {folder}")
    if stopped:
        print(f"Stopped at the rate limit: {stopped}")
    if stopped or failed:
        print(f"To continue later: python -m llm.offline_check run --provider {client.provider} "
              f"--source {source} --resume \"{folder}\"")
    return folder


def score(run_folder):
    run_folder = Path(run_folder)
    with open(run_folder / "answers.jsonl", encoding="utf-8") as f:
        records = {r["case_id"]: r for r in (json.loads(line) for line in f if line.strip())}
    tasks = {c["case_id"]: c["scoring_task"] for c in planted_cases() + real_cases()}
    rows = ["| Case | Errors before | Errors after | Fixed | Still failing | New failures |", "|---|---|---|---|---|---|"]
    totals = {"cases": 0, "before": 0, "after": 0}
    for path in sorted((run_folder / "after").glob("*.json")):
        case_id = path.stem
        if case_id not in records or case_id not in tasks:
            print(f"skipped {path.name}: unknown case")
            continue
        try:
            result = validate(json.loads(path.read_text(encoding="utf-8")), tasks[case_id])
        except ConfigError as exc:
            print(f"skipped {path.name}: invalid configuration ({exc})")
            continue
        before, after = set(records[case_id]["expected_failures"]), set(result.failed_rules)
        totals["cases"] += 1
        totals["before"] += len(before)
        totals["after"] += len(after)
        rows.append(f"| {case_id} | {len(before)} | {len(after)} | {', '.join(sorted(before - after)) or '-'} | "
                    f"{', '.join(sorted(before & after)) or '-'} | {', '.join(sorted(after - before)) or '-'} |")
    summary = (f"{totals['cases']} cases scored: {totals['before']} errors before the advice, "
               f"{totals['after']} after.")
    (run_folder / "scores.md").write_text("# Errors before and after the model's advice\n\n" + summary
                                          + "\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
    print(summary)
    return totals


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline check of the assistant (Step 7).")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--provider", required=True, help="stub for a dry run, or openrouter")
    r.add_argument("--source", choices=["planted", "real", "all"], default="planted")
    r.add_argument("--resume", help="an earlier run folder to continue")
    s = sub.add_parser("score")
    s.add_argument("--run", required=True, help="a run folder under llm/results/")
    args = parser.parse_args(argv)
    llm_client.load_env_file()
    if args.command == "run":
        run(args.provider, args.source, resume=args.resume)
    else:
        score(args.run)


if __name__ == "__main__":
    main()
