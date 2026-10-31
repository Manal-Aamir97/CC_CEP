"""Model clients for the assistant.

Choose with CEP_LLM_PROVIDER (usually set in the .env file; see .env.example):

    openrouter  the real assistant: calls the OpenRouter API; needs OPENROUTER_API_KEY
    none        the assistant panel says it is not available (the default when nothing is set)
    stub        canned replies for testing the panel and the logs; never for real participants

Any other provider = one more class with `provider`, `model` and `complete(prompt) -> Reply`,
registered in PROVIDERS. It must use SETTINGS (build plan: one fixed model, temperature 0, logged).
The API key stays on the server: it is read from the environment, never logged, and never sent to
the browser or written into an error message.
"""
import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"

# Reasoning tokens count against max_tokens, and a reasoning model can use up the whole budget and
# return no answer, so the limit is well above the length of a normal answer and effort stays low.
SETTINGS = {"temperature": 0, "max_output_tokens": 2000, "seed": 2026, "reasoning_effort": "low"}

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
REQUEST_TIMEOUT_S = 60
MAX_ATTEMPTS = 3
RETRY_WAIT_S = 2
MAX_RETRY_WAIT_S = 10
RETRY_STATUS = {408, 429, 500, 502, 503, 524, 529}


def load_env_file(path=None):
    """Read KEY=VALUE lines from .env into the environment. Real environment variables win."""
    path = Path(path) if path else ENV_FILE
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class AssistantUnavailable(RuntimeError):
    """No model is connected, or the connected one failed."""


class RateLimited(AssistantUnavailable):
    """The provider's request limit is used up; waiting (or more credits) is the only fix."""


@dataclass(frozen=True)
class Reply:
    text: str
    provider: str
    model: str
    latency_ms: int
    served_model: str = ""         # the exact model version the provider says it used
    finish_reason: str = ""        # "length" means the answer was cut off by the token limit
    usage: dict = field(default_factory=dict)


class UnavailableClient:
    provider = "none"
    model = "none"
    configured = False

    def complete(self, prompt):
        raise AssistantUnavailable("No AI model is connected yet.")


class StubClient:
    """Deterministic placeholder replies so the panel, logging and analysis can be tested."""
    provider = "stub"
    model = "stub-v1"
    configured = True

    def complete(self, prompt):
        started = time.perf_counter()
        question = prompt["messages"][-1]["content"].rsplit("USER QUESTION:\n", 1)[-1].strip()
        text = (f"[Test assistant, not a real AI model] You asked: \"{question[:200]}\". "
                "When the real model is connected, its answer will appear here.")
        return Reply(text, self.provider, self.model, int((time.perf_counter() - started) * 1000))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it could carry the Authorization header to another host."""

    def redirect_request(self, *args, **kwargs):
        return None


class OpenRouterClient:
    """OpenRouter chat completions over plain HTTPS (standard library, no SDK to install)."""
    provider = "openrouter"
    min_interval_s = 3.2  # free models allow 20 requests a minute; used by the offline check

    def __init__(self, api_key=None, model=None, url=OPENROUTER_URL, retry_wait_s=RETRY_WAIT_S):
        key = os.environ.get("OPENROUTER_API_KEY", "") if api_key is None else api_key
        self._key = key.strip()
        self.model = model or os.environ.get("OPENROUTER_MODEL") or OPENROUTER_DEFAULT_MODEL
        self._url = url
        self._retry_wait_s = retry_wait_s
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __repr__(self):
        return f"OpenRouterClient(model={self.model!r}, configured={self.configured})"

    @property
    def configured(self):
        """False for a blank key or a placeholder such as <OPENROUTER_API_KEY>."""
        return bool(self._key) and not any(c in self._key for c in "<> \t\r\n")

    def payload(self, prompt):
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": prompt["system"]}] + prompt["messages"],
            "temperature": SETTINGS["temperature"],
            "max_tokens": SETTINGS["max_output_tokens"],
            "seed": SETTINGS["seed"],
            "reasoning": {"effort": SETTINGS["reasoning_effort"], "exclude": True},
            # Refuse rather than quietly run on another model or ignore temperature 0.
            "provider": {"allow_fallbacks": False, "require_parameters": True},
            "stream": False,
        }

    def _clean(self, text):
        text = str(text)[:300]
        return text.replace(self._key, "[key]") if self._key else text

    def _failure(self, status, body):
        try:
            message = json.loads(body).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        text = self._clean(f"OpenRouter returned HTTP {status}: {message or 'no details'}")
        hints = {401: " (check OPENROUTER_API_KEY)", 402: " (the account needs credits)",
                 404: " (model or provider not available; check OPENROUTER_MODEL)"}
        text += hints.get(status, "")
        return RateLimited(text) if status == 429 else AssistantUnavailable(text)

    def _send(self, data):
        """One HTTP attempt. Returns (status, body bytes, wait hint in seconds or None)."""
        request = urllib.request.Request(self._url, data=data, method="POST", headers={
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
            "X-Title": "CEP LLM cloud configuration study"})
        try:
            with self._opener.open(request, timeout=REQUEST_TIMEOUT_S) as response:
                return response.status, response.read(), None
        except urllib.error.HTTPError as exc:
            with exc:
                try:
                    wait = float(exc.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    wait = None
                return exc.code, exc.read(), wait

    def complete(self, prompt):
        if not self.configured:
            raise AssistantUnavailable("No OpenRouter API key: paste it into the .env file (OPENROUTER_API_KEY), then restart the console.")
        data = json.dumps(self.payload(prompt)).encode("utf-8")
        started = time.perf_counter()
        problem = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            wait = None
            try:
                status, body, wait = self._send(data)
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, HTTPException) as exc:
                problem = AssistantUnavailable(self._clean(f"Could not reach OpenRouter: {type(exc).__name__}: {exc}"))
            else:
                if status == 200:
                    return self._reply(body, started)
                problem = self._failure(status, body)
                if status not in RETRY_STATUS:
                    raise problem
            if attempt < MAX_ATTEMPTS:
                time.sleep(min(wait if wait is not None else self._retry_wait_s * attempt, MAX_RETRY_WAIT_S))
        raise problem

    def _reply(self, body, started):
        try:
            answer = json.loads(body)
        except ValueError:
            raise AssistantUnavailable("OpenRouter sent a response that could not be read.") from None
        if isinstance(answer, dict) and isinstance(answer.get("error"), dict):  # errors can arrive with HTTP 200
            raise self._failure(answer["error"].get("code", "?"), body)
        try:
            choice = answer["choices"][0]
        except (KeyError, IndexError, TypeError):
            raise AssistantUnavailable("OpenRouter sent a response without an answer.") from None
        if choice.get("error"):
            raise AssistantUnavailable(self._clean(f"The model failed: {choice['error'].get('message', 'no details')}"))
        message = choice.get("message") or {}
        text = (message.get("content") or "").strip() if isinstance(message.get("content"), (str, type(None))) else ""
        finish = choice.get("finish_reason") or ""
        if not text:
            raise AssistantUnavailable(self._clean(f"The model returned no answer (finish_reason: {finish or 'none'}; "
                                                   f"refusal: {message.get('refusal') or 'none'})."))
        return Reply(text, self.provider, self.model, int((time.perf_counter() - started) * 1000),
                     served_model=answer.get("model", ""), finish_reason=finish, usage=answer.get("usage") or {})


PROVIDERS = {"none": UnavailableClient, "stub": StubClient, "openrouter": OpenRouterClient}


def get_client(name=None):
    name = (name or os.environ.get("CEP_LLM_PROVIDER") or "none").strip().lower()
    if name not in PROVIDERS:
        raise ValueError(f"LLM provider {name!r} is not built yet; available: {', '.join(PROVIDERS)}")
    return PROVIDERS[name]()


def describe(client):
    """What goes into the log about the model in use. Never includes the key."""
    return {"provider": client.provider, "model": client.model, **SETTINGS}
