"""The simulated cloud console (build plan Steps 4 to 6).

Participant flow:
    /  ->  /start  ->  consent  ->  background questions  ->  practice task
       ->  part 1 (3 tasks)  ->  break  ->  part 2 (3 tasks)  ->  post-questionnaire  ->  done

Rules this file enforces:
- Pages are built from study.page_task() (plain wording) and the assistant's prompt from
  study.public_task() (exact technical wording). Scoring rules, traps and answers reach neither.
- Participants never see the validator's result. It is written to the server's event log
  (record_event), never into a response or the session cookie (which the browser can read).
- The assistant answers only in the assisted part, and never sees validator findings.
- Researcher preview pages (/preview) exist only when RESEARCHER_PREVIEW is on.
"""
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path

from flask import (Flask, abort, current_app, jsonify, redirect, render_template, request,
                   session, url_for)

from app import study
from app.events import LOG_VERSION, EventLog, now_iso
from llm import client as llm_client
from llm.prompt import MAX_QUESTION_CHARS, build_messages
from validator import config_problems, submission_problems, validate

APP_VERSION = "1.2"
PARTICIPANT_ID = re.compile(r"P\d{2,3}")
MAX_QUESTIONS_PER_TASK = 30
MAX_EVENTS_PER_POST = 200
MAX_CONFIG_BYTES = 50_000
MAX_COMMENT_CHARS = 1000


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("CEP_SECRET_KEY") or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        RESEARCHER_PREVIEW=False,
        LOG_DIR=os.environ.get("CEP_LOG_DIR") or str(Path(__file__).resolve().parents[1] / "logs"),
        LLM_PROVIDER=os.environ.get("CEP_LLM_PROVIDER") or "none",
    )
    if test_config:
        app.config.update(test_config)
    app.extensions["cep_log"] = EventLog(app.config["LOG_DIR"])
    app.extensions["cep_events"] = []          # the same events, in memory, for tests
    app.extensions["cep_llm"] = llm_client.get_client(app.config["LLM_PROVIDER"])
    app.extensions["cep_chats"] = {}           # (participant, task) -> [(question, answer)]

    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'")
        return response

    register_routes(app)
    return app


# ---------------------------------------------------------------- helpers

def current_step():
    if "participant_id" not in session:
        return None
    steps = study.schedule(session["group"])
    return steps[min(session.get("step", 0), len(steps) - 1)]


def advance():
    session["step"] = session.get("step", 0) + 1


def record_event(event_type, part=None, **fields):
    """Append one event to the participant's log file for the current part (and to memory)."""
    step = current_step()
    entry = {"event": event_type, "at": now_iso(),
             "participant_id": session["participant_id"], "group": session["group"],
             "part": part if part is not None else step["part"], **fields}
    current_app.extensions["cep_events"].append(entry)
    current_app.extensions["cep_log"].write(entry)
    return entry


def open_task(task_id):
    """The current step if it is this task, else None."""
    step = current_step()
    if step is None or step["kind"] != "task" or step["task_id"] != task_id:
        return None
    return step


def submission_errors(config, task):
    """Form-level problems only (shape, empty fields). Never a security verdict."""
    if not isinstance(config, dict):
        return ["The form did not send a configuration."]
    problems = config_problems(config)
    if not problems and config["resource"] != task["resource"]:
        problems = [f"This task needs a {task['resource']} configuration."]
    if not problems:
        problems = submission_problems(config)
    return problems


def small_json(value):
    return len(json.dumps(value)) <= MAX_CONFIG_BYTES


def llm_label(client):
    if client.provider == "none":
        return "No AI model is connected: assisted parts will show the assistant as unavailable."
    if client.provider == "stub":
        return "Test assistant (stub) is connected: canned replies only. Never use it with participants."
    if not client.configured:
        return (f"{client.provider} is selected but has no API key: paste it into the .env file and restart. "
                "Assisted parts will show the assistant as unavailable.")
    return (f"AI model selected: {client.provider} / {client.model}. "
            "Run python -m llm.check first to confirm it answers.")


def chat_key(task_id):
    return (session["participant_id"], task_id)


def render_task(task_id, step=None, preview=False, assisted=False):
    task = study.page_task(task_id)
    if preview:
        progress = "Researcher preview"
        submit_url = url_for("preview_submit", task_id=task_id)
        events_url = ""
        ask_url = url_for("preview_assistant", task_id=task_id)
        draft_key = ""
        history = []
    else:
        progress = study.progress_label(step)
        submit_url = url_for("submit", task_id=task_id)
        events_url = url_for("client_events", task_id=task_id)
        ask_url = url_for("assistant", task_id=task_id)
        draft_key = f"cep-draft:{session['participant_id']}:{task_id}"
        history = current_app.extensions["cep_chats"].get(chat_key(task_id), [])
    return render_template(
        "task.html",
        task=task,
        page=study.PAGES[task["resource"]],
        options=study.FORM_OPTIONS,
        js_options={"ingress_types": study.INGRESS_TYPES},
        progress=progress,
        submit_url=submit_url,
        events_url=events_url,
        ask_url=ask_url,
        draft_key=draft_key,
        preview=preview,
        assisted=assisted,
        chat_history=history,
    )


def ask_model(task_id, body, history):
    """Validate a question, build the prompt and ask the model. Returns (status, payload, prompt, reply)."""
    if not isinstance(body, dict):
        return 400, {"error": "Expected a question."}, None, None
    question = body.get("question")
    config = body.get("config")
    if not isinstance(question, str) or not question.strip():
        return 400, {"error": "Type a question first."}, None, None
    if len(question) > MAX_QUESTION_CHARS:
        return 400, {"error": f"Please keep questions under {MAX_QUESTION_CHARS} characters."}, None, None
    task = study.public_task(task_id)
    if not isinstance(config, dict) or config.get("resource") != task["resource"] or not small_json(config):
        return 400, {"error": "The form's settings could not be read. Reload the page."}, None, None
    if len(history) >= MAX_QUESTIONS_PER_TASK:
        return 429, {"error": "You have reached the question limit for this task."}, None, None
    prompt = build_messages(task, config, question, history)
    try:
        reply = current_app.extensions["cep_llm"].complete(prompt)
    except Exception as exc:  # the panel must keep working even if the model fails
        return 503, {"error": "The assistant is not available right now. Please tell the researcher.",
                     "detail": f"{type(exc).__name__}: {exc}"}, prompt, None
    return 200, {"answer": reply.text}, prompt, reply


# ---------------------------------------------------------------- routes

def register_routes(app):

    def start_page(error=None, status=200):
        groups = [(g, study.describe_group(g)) for g in study.groups()]
        return render_template("start.html", groups=groups, active=session.get("participant_id"),
                               preview=False, preview_enabled=app.config["RESEARCHER_PREVIEW"],
                               llm_note=llm_label(app.extensions["cep_llm"]), error=error), status

    @app.get("/")
    def start():
        return start_page()

    @app.post("/start")
    def start_session():
        participant_id = request.form.get("participant_id", "").strip().upper()
        group = request.form.get("group", "")
        if not PARTICIPANT_ID.fullmatch(participant_id) or group not in study.groups():
            return start_page("Enter an ID like P01 and choose a group.", 400)
        if app.extensions["cep_log"].has_participant(participant_id):
            return start_page(f"Log files for {participant_id} already exist. Use a new participant ID.", 400)
        session.clear()
        session.update(participant_id=participant_id, group=group, step=0, started={})
        record_event("session_start", log_version=LOG_VERSION, app_version=APP_VERSION,
                     schedule=study.describe_group(group), llm=llm_client.describe(app.extensions["cep_llm"]))
        return redirect(url_for("next_step"))

    @app.get("/next")
    def next_step():
        step = current_step()
        if step is None:
            return redirect(url_for("start"))
        kind = step["kind"]
        if kind == "task":
            return redirect(url_for("task_page", task_id=step["task_id"]))
        if kind in ("consent", "background", "post"):
            return redirect(url_for(kind))
        if kind == "break":
            return render_template("break.html", part=step["after_part"], preview=False)
        if not session.get("ended"):
            session["ended"] = True
            record_event("session_end")
        return render_template("done.html", preview=False)

    # ------------------------------------------------------------ consent and questionnaires

    def step_is(kind):
        step = current_step()
        return step is not None and step["kind"] == kind

    @app.route("/consent", methods=["GET", "POST"])
    def consent():
        if not step_is("consent"):
            return redirect(url_for("next_step"))
        if request.method == "POST":
            if request.form.get("agree") != "yes":
                return render_template("consent.html", preview=False,
                                       error="Please tick the box to take part, or tell the researcher if you prefer not to."), 400
            record_event("consent", agreed=True)
            advance()
            return redirect(url_for("next_step"))
        return render_template("consent.html", preview=False, error=None)

    @app.route("/background", methods=["GET", "POST"])
    def background():
        if not step_is("background"):
            return redirect(url_for("next_step"))
        years = dict(study.YEARS_OF_STUDY)
        levels = {str(value) for value, _ in study.CLOUD_EXPERIENCE}
        if request.method == "POST":
            year, level = request.form.get("year_of_study"), request.form.get("cloud_experience")
            if year not in years or level not in levels:
                return render_template("background.html", preview=False, years=study.YEARS_OF_STUDY,
                                       levels=study.CLOUD_EXPERIENCE, error="Please answer both questions."), 400
            record_event("background", year_of_study=year, cloud_experience=int(level))
            advance()
            return redirect(url_for("next_step"))
        return render_template("background.html", preview=False, years=study.YEARS_OF_STUDY,
                               levels=study.CLOUD_EXPERIENCE, error=None)

    @app.route("/post", methods=["GET", "POST"])
    def post():
        if not step_is("post"):
            return redirect(url_for("next_step"))
        if request.method == "POST":
            answers = {}
            for key, *_ in study.POST_QUESTIONS:
                value = request.form.get(key)
                if value not in {"1", "2", "3", "4", "5"}:
                    return render_template("post.html", preview=False, questions=study.POST_QUESTIONS,
                                           error="Please answer all three questions."), 400
                answers[key] = int(value)
            comments = request.form.get("comments", "").strip()[:MAX_COMMENT_CHARS]
            record_event("post_questionnaire", comments=comments, **answers)
            advance()
            return redirect(url_for("next_step"))
        return render_template("post.html", preview=False, questions=study.POST_QUESTIONS, error=None)

    @app.post("/break")
    def end_break():
        step = current_step()
        if step is not None and step["kind"] == "break":
            record_event("break_end", after_part=step["after_part"])
            advance()
        return redirect(url_for("next_step"))

    # ------------------------------------------------------------ tasks

    @app.get("/task/<task_id>")
    def task_page(task_id):
        step = open_task(task_id)
        if step is None:
            return redirect(url_for("next_step"))
        started = session.setdefault("started", {})
        if task_id in started:
            record_event("task_reload", task_id=task_id)
        else:
            started[task_id] = now_iso()
            session.modified = True
            record_event("task_start", task_id=task_id, condition=step["condition"], set=step.get("set"),
                         initial_config=study.public_task(task_id)["initial_config"])
        return render_task(task_id, step, assisted=step["condition"] == "assisted")

    @app.post("/api/events/<task_id>")
    def client_events(task_id):
        if open_task(task_id) is None:
            return jsonify(error="This task is no longer open."), 409
        body = request.get_json(silent=True) if request.is_json else None
        items = body.get("events") if isinstance(body, dict) else None
        if not isinstance(items, list) or len(items) > MAX_EVENTS_PER_POST:
            return jsonify(error="Expected a list of events."), 400
        recorded = 0
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("config"), dict) \
                    or not small_json(item):
                continue
            client_at = str(item.get("at", ""))[:40]
            if item.get("type") == "field_change" and isinstance(item.get("path"), str) and len(item["path"]) <= 200:
                record_event("field_change", task_id=task_id, path=item["path"], old=item.get("old"),
                             new=item.get("new"), client_at=client_at, config=item["config"])
                recorded += 1
            elif item.get("type") == "draft_restored":
                record_event("draft_restored", task_id=task_id, client_at=client_at, config=item["config"])
                recorded += 1
        return jsonify(ok=True, recorded=recorded)

    @app.post("/api/assistant/<task_id>")
    def assistant(task_id):
        step = open_task(task_id)
        if step is None:
            return jsonify(error="This task is no longer open. Reload the page."), 409
        if step["condition"] != "assisted":
            return jsonify(error="The assistant is not available in this part."), 403
        if not request.is_json:
            return jsonify(error="Expected a JSON question."), 415
        body = request.get_json(silent=True)
        history = app.extensions["cep_chats"].setdefault(chat_key(task_id), [])
        status, payload, prompt, reply = ask_model(task_id, body, history)
        if prompt is not None:
            record_event("assistant_question", task_id=task_id, question=body["question"],
                         config=body["config"], prompt=prompt)
        if reply is not None:
            history.append((body["question"], reply.text))
            record_event("assistant_answer", task_id=task_id, answer=reply.text, provider=reply.provider,
                         model=reply.model, served_model=reply.served_model, finish_reason=reply.finish_reason,
                         usage=reply.usage, latency_ms=reply.latency_ms)
        elif prompt is not None:
            record_event("assistant_error", task_id=task_id, error=payload.get("detail", ""))
        payload.pop("detail", None)
        return jsonify(payload), status

    @app.post("/api/submit/<task_id>")
    def submit(task_id):
        step = open_task(task_id)
        if step is None:
            return jsonify(problems=["This task is no longer open. Reload the page."]), 409
        if not request.is_json:
            return jsonify(problems=["Expected a JSON configuration."]), 415
        config = request.get_json(silent=True)
        task = study.task(task_id)
        problems = submission_errors(config, task)
        if problems:
            record_event("submit_rejected", task_id=task_id, problems=problems,
                         config=config if isinstance(config, dict) and small_json(config) else None)
            return jsonify(problems=problems), 400

        started = session.get("started", {}).get(task_id)
        duration = None
        if started:
            duration = round((datetime.fromisoformat(now_iso()) - datetime.fromisoformat(started)).total_seconds(), 3)
        result = validate(config, task).to_dict() if task["scored"] else None
        record_event("task_submit", task_id=task_id, condition=step["condition"], set=step.get("set"),
                     scored=task["scored"], duration_s=duration, config=config, result=result)
        advance()
        return jsonify(ok=True, next=url_for("next_step"))

    # ------------------------------------------------------------ researcher preview

    def require_preview():
        if not app.config["RESEARCHER_PREVIEW"]:
            abort(404)

    @app.get("/preview")
    def preview_index():
        require_preview()
        tasks = [study.public_task(t) for t in study.all_task_ids()]
        return render_template("preview_index.html", tasks=tasks, preview=True)

    @app.get("/preview/<task_id>")
    def preview_task(task_id):
        require_preview()
        if task_id not in study.all_task_ids():
            abort(404)
        return render_task(task_id, preview=True, assisted=request.args.get("assisted") == "1")

    @app.post("/api/preview/<task_id>")
    def preview_submit(task_id):
        require_preview()
        if task_id not in study.all_task_ids():
            abort(404)
        task = study.task(task_id)
        config = request.get_json(silent=True) if request.is_json else None
        problems = submission_errors(config, task)
        if problems:
            return jsonify(problems=problems), 400
        result = validate(config, task).to_dict() if task["scored"] else None
        return jsonify(ok=True, result=result)

    @app.post("/api/preview/assistant/<task_id>")
    def preview_assistant(task_id):
        """Lets researchers try the assistant on any task. Nothing is logged or remembered."""
        require_preview()
        if task_id not in study.all_task_ids():
            abort(404)
        status, payload, _, _ = ask_model(task_id, request.get_json(silent=True), [])
        payload.pop("detail", None)
        return jsonify(payload), status
