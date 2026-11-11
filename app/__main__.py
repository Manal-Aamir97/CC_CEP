"""Start the console:  python -m app  [--preview] [--port 5000]

Binds to 127.0.0.1 by default, so only the lab PC itself can open it.
Reads the model settings and API key from the .env file in the repository root, once at start:
restart the console after changing that file.

Logs go to logs/ unless CEP_LOG_DIR names another folder; the pilot uses CEP_LOG_DIR=pilot/logs so
its sessions never mix with study data. The folder in use is printed at start.
"""
import argparse
from pathlib import Path

from app.app import create_app
from llm.client import get_client, load_env_file

load_env_file()
assistant = get_client()
print(f"Assistant: {assistant.provider} ({assistant.model}): "
      f"{'ready' if assistant.configured else 'NOT READY, participants will see it as unavailable; check .env'}", flush=True)

parser = argparse.ArgumentParser(description="Run the simulated cloud console.")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=5000)
parser.add_argument("--preview", action="store_true",
                    help="enable researcher preview pages (/preview); never during a participant session")
args = parser.parse_args()

app = create_app({"RESEARCHER_PREVIEW": args.preview})
print(f"Logs: {Path(app.config['LOG_DIR']).resolve()}", flush=True)
app.run(host=args.host, port=args.port, debug=False)
