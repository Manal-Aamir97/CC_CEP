"""The structured prompt the assistant receives (build plan Step 5).

A prompt holds the task's exact technical wording, the form's current JSON, the words the participant
sees on the form, and their question. Participants see plain wording only (study.page_task). A prompt
never holds validator findings, scoring rules, traps or reference answers. build_messages() accepts
only study.public_task() data and refuses anything else, and llm/test_llm.py checks that no rule ID or
answer ever appears in a prompt.
"""
import json

SYSTEM_PROMPT = ("You are a cloud security assistant. Explain risks and recommend changes. "
                 "Do not claim the configuration is secure or insecure as a final verdict. "
                 "The person you are helping has no cloud experience. Keep answers short and use simple everyday "
                 "words. They fill in a simple form and never see the technical wording or the JSON, so do not "
                 "show JSON or code. Tell them what to do using the form's own words, listed under WHAT THE PERSON "
                 "SEES. Do not use terms such as SSH, RDP, HTTP, HTTPS, port, IP, CIDR or bucket, unless they ask "
                 "what one means; say \"internet address\" for an IP address.")
PUBLIC_KEYS = {"task_id", "resource", "scored", "participant", "initial_config"}
MAX_QUESTION_CHARS = 1000
MAX_HISTORY_TURNS = 6

# The form's words for each configuration field. app/test_app.py checks them against the rendered form.
_RULE_ROWS = ('"source": "0.0.0.0/0" appears as "Anyone on the internet". Any other source, even an empty one, '
              'appears as "Only this address" with a box for the address. An empty source means the box is empty. '
              'A person types a single address on its own, for example 192.0.2.10, so never mention /32. '
              'Buttons: "Add a row" and "Remove".')
FORM_GUIDES = {
    "web_server": "\n".join([
        'name: "Server name". os: "Operating system". instance_type: "Size".',
        'public_ip: tick box "Give this server a public internet address".',
        'ingress: table "Who can connect to this server", with columns "What it is for" and "Who can connect".',
        'Ports in "What it is for": 22 is "Remote login (to manage the server)", 80 is "Website visitors (http://)", '
        '443 is "Website visitors (https://)", 3389 is "Remote desktop (to control the server\'s screen)". '
        'Any other port is "Other (type a service number)".',
        _RULE_ROWS,
        'disk_encryption: tick box "Encrypt the server\'s disk".',
    ]),
    "storage_bucket": "\n".join([
        'A bucket is called a "storage space". bucket_name: "Storage name". region: "Location".',
        'block_public_access: section "Public access" with a tick box "Block all public access" that turns on all four settings:',
        'block_public_acls: "Refuse any new sharing setting that would make a file public". '
        'ignore_public_acls: "Ignore every sharing setting that makes a file public". '
        'block_public_policy: "Refuse any new rule for the whole storage space that would make files public". '
        'restrict_public_buckets: "Ignore every rule for the whole storage space that makes files public".',
        'versioning: section "Earlier versions of files", choices "Do not keep earlier versions" and "Keep earlier versions".',
        'encryption: section "Encryption", choices "No encryption" (none), '
        '"Encrypt, with keys managed for you by the cloud provider" (sse-s3) and '
        '"Encrypt, with keys kept in a separate key service" (sse-kms).',
        'access_logging: section "Access records", choices "Do not keep records" and "Keep records". '
        'target_bucket: "Storage space to save the records in".',
    ]),
    "database": "\n".join([
        'identifier: "Database name". engine: "Version". instance_class: "Size".',
        'publicly_accessible: question "Can the database be reached from the internet?", '
        'choices starting "Yes: it gets a public internet address" and "No: only computers inside your private network can connect".',
        'ingress: table "Who can connect to this database", with columns "What it is for" and "Who can connect". '
        'The port is not shown; every row says "Connecting to the database".',
        _RULE_ROWS,
        'storage_encrypted: tick box "Encrypt the database\'s stored data".',
        'backup_retention_days: "Keep automatic backups for", where 0 appears as "0 days (no backups)".',
    ]),
}


def task_requirements(public_task):
    p = public_task["participant"]
    lines = [p["title"], "", p["scenario"], ""]
    lines += [f"{i}. {r['text']}" for i, r in enumerate(p["requirements"], 1)]
    if p.get("notes"):
        lines += ["", p["notes"]]
    return "\n".join(lines)


def user_message(public_task, config, question):
    return ("TASK REQUIREMENTS:\n" + task_requirements(public_task)
            + "\n\nCURRENT CONFIGURATION:\n" + json.dumps(config, indent=2)
            + "\n\nWHAT THE PERSON SEES:\n" + FORM_GUIDES[public_task["resource"]]
            + "\n\nUSER QUESTION:\n" + question.strip())


def build_messages(public_task, config, question, history=()):
    """The full prompt: system text, up to 6 earlier turns of this task, then the structured question.

    history = earlier (question, answer) pairs from the same task, oldest first. Only the newest
    message carries the task text and configuration, so the model always sees the current form.
    """
    extra = set(public_task) - PUBLIC_KEYS
    if extra:
        raise ValueError(f"prompt may only use public task data, got {sorted(extra)}")
    if not question.strip():
        raise ValueError("empty question")
    messages = []
    for asked, answered in list(history)[-MAX_HISTORY_TURNS:]:
        messages += [{"role": "user", "content": asked}, {"role": "assistant", "content": answered}]
    messages.append({"role": "user", "content": user_message(public_task, config, question)})
    return {"system": SYSTEM_PROMPT, "messages": messages}
