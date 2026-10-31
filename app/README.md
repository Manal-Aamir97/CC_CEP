# Steps 4 to 6: Form-based interface, assistant panel and event log

A small Flask app for people with no cloud knowledge. Each task is one page: the task text at the top, form controls in the middle, and a Create button. On every change, the page rebuilds the configuration JSON defined in [`schema/config.schema.json`](../schema/config.schema.json).

**Plain words on screen, exact details behind the scenes.** Screens never show terms such as SSH, port, CIDR, bucket or `0.0.0.0/0`. For example, a source of `0.0.0.0/0` appears as "Anyone on the internet", and port 22 appears as "Remote login (to manage the server)". The JSON, the validator and the logs are unchanged and keep the exact values. The AI receives the exact technical task text and the JSON, plus the form's words so it can say which label to change. Tests check for these terms on every participant page and check that the words the AI is told match the form.

| File | Contents |
|---|---|
| `app.py` | Routes, session flow, submit endpoint, assistant endpoint, researcher preview |
| `study.py` | Participant schedule (from `tasks/sets.json`), the public view of a task, form option labels, questionnaires |
| `events.py` | The event log: one append-only JSON-lines file per participant and session part |
| `templates/` | `task.html` plus one form per resource in `templates/forms/`; start, consent, background, break, questionnaire and done pages |
| `static/form.js` | Builds the JSON from the form; form-level checks; submit |
| `static/events.js`, `static/assistant.js` | Send field changes to the log; the assistant panel |
| `static/markdown.js` | Formats the model's answers (bold, lists, tables). Builds text nodes only, never HTML, and shows links as plain words |
| `static/console.css` | Styling (no external fonts or scripts) |
| `test_app.py` | Tests (below) |

## Run

From the repository root (needs `pip install -r requirements.txt` once):

```bash
python -m app
```

Open http://127.0.0.1:5000, enter a participant ID (P01) and a counterbalancing group, then hand the computer over. The server listens on 127.0.0.1 only, so other computers cannot reach it.

To check tasks yourself before the study, start it with researcher preview:

```bash
python -m app --preview
```

Then open http://127.0.0.1:5000/preview. Any task can be opened, changed and submitted; the validator's verdict and the live JSON appear under the form, and nothing is recorded. **Never run with `--preview` during a participant session.**

## Participant flow

Start page (researcher) -> consent -> background questions -> practice task P0 -> part 1 (three tasks) -> break page -> part 2 (three tasks) -> post-study questions -> done page.

The order and condition of each part come from the participant's group in `tasks/sets.json`. A task page can only be opened or submitted when it is the participant's current step. Going back or skipping ahead redirects to the right page.

## What the participant never sees

- **Scoring, traps and answers.** Pages are built only from `study.page_task()` (task ID, resource, plain task text, starting form). A test renders every task and checks that no rule ID, trap text, scoring parameter or research term appears in the HTML.
- **The technical task text.** It goes only to the AI (`study.public_task()`), and a test checks that it is not in the page.
- **The validator's result.** The submit endpoint scores the configuration and writes the result to the server's log (`record_event`). The response contains only `ok` and the next URL. Results are never put in the session cookie, because Flask's cookie is signed but readable by the browser; a test decodes the cookie to check this.
- **Security hints.** The form only checks that fields are filled in and well formed ("enter an address", "choose one option"). The same checks run in both conditions, and they never say which value is secure. Short explanations say what a setting does, never which choice to make. The example address in the placeholder (`192.0.2.10`) belongs to no task, so it cannot favour one set; a test checks this.

## Form behaviour

- A bare address is stored as a CIDR block but shown as typed: `203.0.113.25` is saved as `203.0.113.25/32` (IPv6: `/128`).
- "Who can connect" rows: add, remove, and pick what the row is for. "Other" asks for a service number; the others keep their port out of sight. On the database, every row uses the database port, which is not shown.
- Each row's "Who can connect" is either "Anyone on the internet" (`0.0.0.0/0`) or "Only this address" with a box. Typing `0.0.0.0/0` in the box switches the row to "Anyone on the internet", so what is on screen always matches the JSON.
- Storage: "Block all public access" ticks or unticks all four settings and shows a partial state when only some are on. The records-storage field is greyed out while access records are off.
- Fields that start empty (encryption, versioning, the database source) must be filled in before Submit works.
- Submit asks for confirmation ("You cannot change it afterwards").
- If the page is reloaded, the participant's unsent changes come back. They are kept in the browser tab's session storage and cleared on submit.

## The assistant panel (Step 5)

The panel appears only on assisted tasks. It sends the question and the form's current JSON to `/api/assistant/<task>`, and the server builds the prompt from public task data only. Answers are displayed with their formatting, and the log keeps the model's raw text. The model comes from `CEP_LLM_PROVIDER` in `.env` (see [`llm/README.md`](../llm/README.md)), and `python -m app` reads that file at start. If the model fails or a limit is reached, the panel says the assistant is unavailable and the participant can carry on.

## The event log (Step 6)

Files are written to `logs/P01_session0.jsonl`, `..._session1.jsonl` and so on, and nothing in them is overwritten. They record consent and questionnaire answers, task starts, every field change (old value, new value and a snapshot), every question with its full prompt and answer, rejected submissions, and each final submission with the validator's result and the time taken. An answer's record also has the model that served it, the finish reason and token usage. The API key is never logged.

## Tests

```bash
python -m unittest app.test_app -v
```

They cover:

- the schedule for all four groups, including a full session per group;
- task order enforcement;
- that results stay out of responses and the cookie;
- that incomplete, invalid and wrong-resource configs are refused without being scored;
- that pages carry only public task data;
- that preview is off by default and records nothing when on;
- that form options match the schema;
- the placeholder check;
- the assistant panel and the event log.

The JavaScript was also exercised in a real browser on A1, A2, A3 and the practice flow. That covered hydration, normalisation, the "Block all" box, radio choices, adding and removing rules, Enter-to-submit, reload recovery, and moving from P0 to A1.

## Known limits (fine for a pilot, revisit before the study)

- The session cookie's signing key is random at each start unless `CEP_SECRET_KEY` is set. A restart therefore ends any open session; the researcher can start it again from the start page.
