# cep-llm-config

A simulated cloud console for the CEP study **"Can LLM Assistance Reduce Human Error in Cloud Security Configuration?"**
Manal Aamir (SE-23070), Syed Abdullah Hyder (SE-23091).

Participants configure cloud resources through a form written in plain words, so no cloud knowledge is needed. In the assisted condition they can also ask an LLM for advice; the LLM receives the exact technical details, which are not displayed. A rule-based validator, which never calls the LLM or reads its output, scores the submitted JSON. Nothing is deployed to real AWS: the form only produces a JSON configuration.

## Layout (from the build plan)

| Folder | Contents | Build step | Status |
|---|---|---|---|
| `tasks/` | Task text, starting forms, traps, scoring rules | 1 | Done |
| `schema/` | `config.schema.json`, the JSON shape of each resource | 2 | Done |
| `validator/` | `schema.py`, `rules.py`, `validator.py`, `test_rules.py` | 3 | Done |
| `app/` | Flask app, form templates, static files, assistant panel, event log | 4, 5, 6 | Done |
| `llm/` | Prompt, OpenRouter client, offline check | 5, 7 | Built; real API tested on 2026-11-01; offline check not run yet |
| `dataset/` | Human-written AWS Terraform (Verdet et al. 2025): real-world baseline and Step 7 cases | 7, 10 | Baseline done (369 projects); 30 projects fetched, hand conversion not started |
| `logs/` | One JSON-lines file per participant session | 6, 9 | Created when sessions run |
| `analysis/` | `analyse.py`, statistics | 10 | Built and tested on synthetic logs; no real data yet |

## Set up and run

Python 3. The tasks, schema and validator need only the standard library; the web app needs Flask:

```bash
pip install -r requirements.txt
```

Start the console (then open http://127.0.0.1:5000):

```bash
python -m app
```

To connect the AI assistant, paste your OpenRouter key into `.env` after `OPENROUTER_API_KEY=`, then send one test question:

```bash
python -m llm.check
```

See [llm/README.md](llm/README.md) for the limits and the data policy.

## Checks

Run from this folder:

```bash
python tasks/check_tasks.py --write-sheet
```

```bash
python -m unittest validator.test_rules app.test_app llm.test_llm llm.test_openrouter dataset.test_dataset analysis.test_analysis
```

Score one configuration file against a task:

```bash
python -m validator A1 path/to/config.json
```

Read more: [tasks/README.md](tasks/README.md) (task design), [tasks/TASKS.md](tasks/TASKS.md) (task sheet), [schema/README.md](schema/README.md) (fields), [validator/README.md](validator/README.md) (rules, metrics, tests), [app/README.md](app/README.md) (the console, participant flow), [llm/README.md](llm/README.md) (the assistant, offline check), [dataset/README.md](dataset/README.md) (real-world data), [analysis/README.md](analysis/README.md) (statistics).
