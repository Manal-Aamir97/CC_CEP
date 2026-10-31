"""Make SYNTHETIC logs by driving the real console with made-up participants. For testing only.

    python -m analysis.make_synthetic_logs --out analysis/synthetic_logs --n 24

Every made-up participant goes through the real web app (consent, background, practice, both
parts, questionnaire) with the stub assistant, so the logs have exactly the real format.
Participants get IDs from P901 upwards, and the folder gets a SYNTHETIC_DATA.txt marker that
makes the analysis print a warning. These numbers are invented and must never be reported.
"""
import argparse
import copy
import random
from pathlib import Path

from analysis.analyse import SYNTHETIC_MARKER
from app import study
from app.app import create_app
from llm.offline_cases import MUTATIONS


def make_config(task, error_chance, rng):
    config = copy.deepcopy(task["reference_solution"])
    for mutate in MUTATIONS[task["resource"]].values():
        if rng.random() < error_chance:
            mutate(config, task["scoring"]["params"])
    return config


def run_participant(app, pid, group, rng):
    client = app.test_client()
    client.post("/start", data={"participant_id": pid, "group": group})
    experience = rng.randint(1, 5)
    for step in study.schedule(group):
        kind = step["kind"]
        if kind == "consent":
            client.post("/consent", data={"agree": "yes"})
        elif kind == "background":
            client.post("/background", data={"year_of_study": rng.choice(["3", "4"]), "cloud_experience": str(experience)})
        elif kind == "task":
            task = study.task(step["task_id"])
            client.get(f"/task/{step['task_id']}")
            if not task["scored"]:
                client.post(f"/api/submit/{step['task_id']}", json=task["reference_solution"])
                continue
            assisted = step["condition"] == "assisted"
            if assisted:
                for i in range(rng.choice([0, 1, 1, 2, 3])):
                    client.post(f"/api/assistant/{step['task_id']}",
                                json={"question": f"Synthetic question {i + 1}", "config": task["initial_config"]})
            chance = max(0.05, 0.45 - 0.06 * (experience - 1)) * (0.6 if assisted else 1.0)
            final = make_config(task, chance, rng)
            changes = [{"type": "field_change", "path": "synthetic", "old": None, "new": None,
                        "at": "synthetic", "config": final} for _ in range(rng.randint(2, 8))]
            client.post(f"/api/events/{step['task_id']}", json={"events": changes})
            response = client.post(f"/api/submit/{step['task_id']}", json=final)
            assert response.status_code == 200, response.get_json()
        elif kind == "break":
            client.post("/break")
        elif kind == "post":
            client.post("/post", data={"helpfulness": str(rng.randint(2, 5)), "trust": str(rng.randint(1, 5)),
                                       "confidence": str(rng.randint(2, 5)), "comments": ""})
    client.get("/next")


def generate(out_dir, n=24, seed=7):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.glob("P*_session*.jsonl")):
        raise SystemExit(f"{out_dir} already holds logs; use an empty folder.")
    (out_dir / SYNTHETIC_MARKER).write_text("SYNTHETIC DATA made by analysis/make_synthetic_logs.py. "
                                            "Not study results. Never report.\n", encoding="utf-8")
    app = create_app({"TESTING": True, "SECRET_KEY": "synthetic", "LOG_DIR": str(out_dir), "LLM_PROVIDER": "stub"})
    rng = random.Random(seed)
    groups = study.groups()
    for i in range(n):
        run_participant(app, f"P{901 + i}", groups[i % len(groups)], rng)
    print(f"wrote synthetic logs for {n} participants to {out_dir}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Make synthetic logs for testing the analysis.")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "synthetic_logs"))
    parser.add_argument("--n", type=int, default=24)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    if not 1 <= args.n <= 99:
        raise SystemExit("--n must be between 1 and 99")
    generate(args.out, args.n, args.seed)


if __name__ == "__main__":
    main()
