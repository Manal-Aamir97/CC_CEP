"""Study logic the web app needs: the participant schedule and what a page may show.

Nothing here talks to the browser directly; app.py renders what these functions return.
"""
import copy
import json
from functools import lru_cache
from pathlib import Path

from validator import load_task

ROOT = Path(__file__).resolve().parents[1]

# scoring, design and reference_solution are researcher-only (tasks/README.md, "Who sees what").
# The LLM gets the exact technical wording (participant); the page shows the plain wording (display).
PUBLIC_KEYS = ("task_id", "resource", "scored", "participant", "initial_config")
PAGE_KEYS = ("task_id", "resource", "scored", "display", "initial_config")


@lru_cache(maxsize=1)
def sets():
    return json.loads((ROOT / "tasks" / "sets.json").read_text(encoding="utf-8-sig"))


def groups():
    return tuple(sets()["counterbalancing_groups"])


def all_task_ids():
    s = sets()
    return [s["practice"]] + s["sets"]["A"] + s["sets"]["B"]


@lru_cache(maxsize=None)
def task(task_id):
    if task_id not in all_task_ids():
        raise KeyError(task_id)
    return load_task(task_id)


def public_task(task_id):
    """What the LLM may see."""
    full = task(task_id)
    return {key: copy.deepcopy(full[key]) for key in PUBLIC_KEYS}


def page_task(task_id):
    """What a participant's page is built from."""
    full = task(task_id)
    return {key: copy.deepcopy(full[key]) for key in PAGE_KEYS}


def schedule(group):
    """Every step a participant in this counterbalancing group goes through, in order.

    'part' also names the log file: 0 = consent, background and practice; 1 and 2 = the two
    parts; 3 = post-questionnaire and end.
    """
    s = sets()
    steps = [{"kind": "consent", "part": 0}, {"kind": "background", "part": 0},
             {"kind": "task", "task_id": s["practice"], "condition": "practice", "part": 0,
              "index": 1, "of": 1}]
    parts = s["counterbalancing_groups"][group]
    for part, block in enumerate(parts, 1):
        task_ids = s["sets"][block["set"]]
        for index, task_id in enumerate(task_ids, 1):
            steps.append({"kind": "task", "task_id": task_id, "condition": block["condition"],
                          "part": part, "set": block["set"], "index": index, "of": len(task_ids)})
        if part < len(parts):
            steps.append({"kind": "break", "after_part": part, "part": part})
    last = len(parts) + 1
    steps += [{"kind": "post", "part": last}, {"kind": "done", "part": last}]
    return steps


# Questionnaires (build plan, protocol): values are what the log stores.
YEARS_OF_STUDY = [("1", "1st year"), ("2", "2nd year"), ("3", "3rd year"), ("4", "4th year"),
                  ("pg", "Postgraduate"), ("other", "Other")]
CLOUD_EXPERIENCE = [(1, "None: I have never done this"),
                    (2, "I have tried it once or twice"),
                    (3, "I have done it in a course or project"),
                    (4, "I do it regularly"),
                    (5, "I do it as part of my job")]
POST_QUESTIONS = [
    ("helpfulness", "How helpful was the assistant in the part where it was available?",
     "Not helpful at all", "Very helpful"),
    ("trust", "How much did you trust the assistant's advice?", "Not at all", "Completely"),
    ("confidence", "How confident are you that the settings you submitted were safe?",
     "Not at all confident", "Very confident"),
]


def describe_group(group):
    parts = sets()["counterbalancing_groups"][group]
    return "; ".join(f"part {n}: Set {p['set']}, {p['condition']}" for n, p in enumerate(parts, 1))


# ---------------------------------------------------------------- what the form shows

# Every word here is for someone who knows nothing about cloud services, and none may hint at which
# value is safe. The AI is told these same words (llm/prompt.py, FORM_GUIDES; a test keeps them equal).
PAGES = {
    "web_server": {"service": "Servers", "heading": "Set up a web server", "submit": "Create server"},
    "storage_bucket": {"service": "Storage", "heading": "Set up online storage", "submit": "Create storage"},
    "database": {"service": "Databases", "heading": "Set up a database", "submit": "Create database"},
}

# Option values must equal the schema's enums (app/test_app.py checks this); labels are display text.
FORM_OPTIONS = {
    "instance_type": [
        ("t2.micro", "1 processor, 1 GB memory (t2.micro)"),
        ("t2.small", "1 processor, 2 GB memory (t2.small)"),
        ("t3.micro", "2 processors, 1 GB memory (t3.micro)"),
        ("t3.small", "2 processors, 2 GB memory (t3.small)"),
        ("t3.medium", "2 processors, 4 GB memory (t3.medium)"),
    ],
    "os": [
        ("ubuntu-22.04", "Ubuntu 22.04"),
        ("ubuntu-24.04", "Ubuntu 24.04"),
        ("debian-12", "Debian 12"),
        ("amazon-linux-2023", "Amazon Linux 2023"),
    ],
    "region": [
        ("us-east-1", "United States (Virginia)"),
        ("eu-west-1", "Europe (Ireland)"),
        ("eu-west-2", "Europe (London)"),
        ("ap-south-1", "Asia (Mumbai)"),
    ],
    "engine": [
        ("postgres-15", "PostgreSQL 15"),
        ("postgres-16", "PostgreSQL 16"),
        ("postgres-17", "PostgreSQL 17"),
    ],
    "instance_class": [
        ("db.t3.micro", "2 processors, 1 GB memory (db.t3.micro)"),
        ("db.t3.small", "2 processors, 2 GB memory (db.t3.small)"),
        ("db.t4g.micro", "2 processors, 1 GB memory (db.t4g.micro)"),
        ("db.t4g.small", "2 processors, 2 GB memory (db.t4g.small)"),
    ],
    "encryption": [
        ("none", "No encryption"),
        ("sse-s3", "Encrypt, with keys managed for you by the cloud provider"),
        ("sse-kms", "Encrypt, with keys kept in a separate key service"),
    ],
    "versioning": [
        ("disabled", "Do not keep earlier versions"),
        ("enabled", "Keep earlier versions"),
    ],
    "backup_retention_days": [(str(days), "0 days (no backups)" if days == 0 else f"{days} day" if days == 1 else f"{days} days")
                              for days in range(36)],
}

# Presets for the web server's "What it is for" column (port None = type your own).
INGRESS_TYPES = [
    {"key": "ssh", "label": "Remote login (to manage the server)", "port": 22},
    {"key": "http", "label": "Website visitors (http://)", "port": 80},
    {"key": "https", "label": "Website visitors (https://)", "port": 443},
    {"key": "rdp", "label": "Remote desktop (to control the server's screen)", "port": 3389},
    {"key": "custom", "label": "Other (type a service number)", "port": None},
]


def progress_label(step):
    if step["condition"] == "practice":
        return "Practice task (not scored)"
    return f"Part {step['part']} - Task {step['index']} of {step['of']}"
