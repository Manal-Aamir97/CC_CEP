"""Check the connection to the real model after pasting the key into .env:

    python -m llm.check

Sends one real test question (it uses one request from the daily limit) and prints the answer,
the exact model used and the token counts. It never prints the key.
"""
import sys

from app import study
from llm import client as llm_client
from llm.prompt import build_messages


def main():
    llm_client.load_env_file()
    client = llm_client.get_client()
    print(f"provider: {client.provider}\nmodel:    {client.model}\nkey set:  {'yes' if client.configured else 'NO'}")
    if client.provider == "none" or not client.configured:
        print("Paste your key into the .env file (OPENROUTER_API_KEY=...) and set CEP_LLM_PROVIDER=openrouter.")
        return 1
    task = study.public_task("A1")
    prompt = build_messages(task, task["initial_config"], "Is my SSH rule safe for this task?")
    try:
        reply = client.complete(prompt)
    except llm_client.AssistantUnavailable as exc:
        print(f"FAILED: {exc}")
        return 1
    usage = reply.usage or {}
    details = usage.get("completion_tokens_details") or {}
    print(f"served:   {reply.served_model}\nfinished: {reply.finish_reason}\ntime:     {reply.latency_ms / 1000:.1f} s")
    print(f"tokens:   {usage.get('prompt_tokens')} in, {usage.get('completion_tokens')} out "
          f"({details.get('reasoning_tokens')} reasoning)\n\n--- answer ---\n{reply.text}\n--- end ---")
    if reply.finish_reason == "length":
        print("\nWARNING: the answer was cut off by the token limit (SETTINGS in llm/client.py).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
