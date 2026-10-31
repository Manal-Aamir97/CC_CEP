# Steps 5 and 7: the AI assistant

The assistant calls NVIDIA Nemotron 3 Ultra through OpenRouter. The real API was tested on 2026-11-01 through `python -m llm.check`, a participant session and the panel. Answers took between 11 and 33 seconds. The automatic tests use a fake local server, so they use up no requests.

| File | Contents |
|---|---|
| `prompt.py` | Builds the structured prompt: system rules, then TASK REQUIREMENTS, CURRENT CONFIGURATION and USER QUESTION (build plan Step 5) |
| `client.py` | Model clients, chosen by `CEP_LLM_PROVIDER`: `openrouter` (the real assistant), `none` ("not available") and `stub` (canned test replies) |
| `check.py` | `python -m llm.check`: sends one real test question and shows what came back |
| `offline_cases.py` | 30 planted-error configurations from our tasks, plus the hand-converted real cases |
| `offline_check.py` | The Step 7 offline check: asks the model about every case, writes answers, a coding sheet and scores |
| `test_llm.py`, `test_openrouter.py` | Tests (no real network; the second uses a fake OpenRouter server on this computer) |

## Connect the real model

1. Open `.env` in the repository root and paste the key straight after `OPENROUTER_API_KEY=`, with no quotes or spaces. `.env` is listed in `.gitignore`, and `.env.example` shows the layout.
2. From the repository root, send one test question:

```bash
python -m llm.check
```

It prints the model that answered, the finish reason, the tokens used and the answer. It never prints the key.

The console reads `.env` once when it starts, so **restart it after changing that file**. Its first line of output says whether the assistant is ready. Then try the panel in researcher preview (`python -m app --preview`, then `/preview/A1?assisted=1`).

**The key:** it is read on the server only, and is never logged, sent to the browser or written into an error message. Redirects are refused so that the key cannot follow one to another host. This project sits in a OneDrive folder, so `.env` is synced to OneDrive too. Do not share that folder, and delete the key on OpenRouter after the study.

We use OpenRouter's HTTPS API from Python's standard library rather than the `@openrouter/sdk` JavaScript package. The model and the service are the same, and the server stays in Python with nothing new to install.

## The model and its limits (checked 2026-10-31)

| | |
|---|---|
| Model | `nvidia/nemotron-3-ultra-550b-a55b:free`, listed by OpenRouter as "NVIDIA: Nemotron 3 Ultra (free)" |
| Exact version | OpenRouter's canonical name is `nvidia/nemotron-3-ultra-550b-a55b-20260604`. Each logged answer records the model name the provider returned as `served_model` |
| Free limits | 20 requests a minute. 50 requests a day if the account has bought under about 10 credits; 1,000 a day with at least that many |
| Data policy | OpenRouter's listing does not state one for this free endpoint, so whether questions are logged or used for training is unconfirmed. The consent page tells participants that questions go to an outside AI service and asks them not to type personal details. A restrictive privacy setting on the OpenRouter account may block free models |

**The daily limit matters.** The offline check alone uses 30 requests. At about 5 questions per assisted task, one participant uses about 15, so 50 a day covers only about 3 participants. Retried requests may use more. When the limit is reached, the panel tells the participant the assistant is unavailable, which would spoil that session, so plan sessions around the limit or buy the credits.

**Do not change the model or its settings once the pilot starts.** Free models can also be withdrawn. OpenRouter currently lists no expiry date for this one, but check again before the study.

## What the model sees, and what it never sees

| Sees | Never sees |
|---|---|
| The exact technical task text (`participant` in the task file), which participants do not see | Validator findings or scores |
| The form's current JSON | Scoring rules, traps or reference answers |
| The form's plain words (`FORM_GUIDES`), so it can name the label to change | Anything from other tasks or other participants |
| The question, plus up to 6 earlier turns of the same task | |

`build_messages()` refuses full task files. The tests check that no rule ID or finding ever appears in a prompt.

The system prompt tells the model that the person has no cloud experience, to keep answers short, to use the form's words, and to avoid terms such as SSH or port unless asked. **This is an instruction, not a guarantee.** In testing, an answer still said "bucket" once, and two answers wrongly said an empty address row was open to "Anyone on the internet". That kind of error is what the offline check and the coding step are there to measure, so report it rather than hide it.

Settings for the whole study (`client.SETTINGS`):

- temperature 0 and a fixed seed;
- low reasoning effort;
- at most 2000 output tokens. Reasoning counts against this limit, and a lower limit risks an empty answer.

Fallbacks are switched off, so the request fails rather than quietly running on another model or ignoring a setting. Each session log records the provider, model and settings, and each answer records `served_model`, `finish_reason` and token usage. Participants may ask up to 30 questions per task.

## Adding another provider

1. In `client.py`, add a class with `provider`, `model` (the exact version string), `configured` and `complete(prompt) -> Reply`. `prompt` is `{"system": str, "messages": [{"role": "user"|"assistant", "content": str}, ...]}`.
2. Read the API key from the environment inside that class, and use `SETTINGS`.
3. Register the class in `PROVIDERS`, then set `CEP_LLM_PROVIDER=<name>` in `.env`.

## The offline check (Step 7)

Commands, run from the repository root:

```bash
python -m llm.offline_check run --provider stub
```

```bash
python -m llm.offline_check score --run llm/results/<run folder>
```

The first is a dry run. With the real model, use `--provider openrouter`, adding `--source all` once the real cases are converted. Requests are paced to the 20-a-minute limit. If the daily limit is reached, the run stops and saves what it has. Continue the next day with `--resume llm/results/<run folder>`, which asks only the cases not yet answered.

Each run writes `answers.jsonl` (prompt, answer, model, and the validator's hidden findings) and `coding.csv` for two researchers to fill in. Coding steps:

1. For each real error, mark whether the advice mentions it and whether the advice is correct.
2. Add one row per case for any wrong or harmful advice.
3. In the preview console, apply exactly what the model advised.
4. Save the resulting configuration as `after/<case_id>.json`.
5. Run `score` to see errors before and after.

The `auto_hint` column is a keyword guess to speed up coding, never a verdict.
