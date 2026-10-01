"""Local model backend.

The only supported backend talks to an Ollama server on loopback. It is optional
on purpose: the tutor is designed to be *useful with no model at all*. Without a
model it runs in extractive mode - it quotes the course pack, asks the pack's
questions, marks them, and gives the pack's explanations. The model, when it is
there, changes how things are said, never what is true.

That split is what makes the "if it doesn't know, it says so" promise keepable.
The facts come from the pack; the model is a phrasing layer over them.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

_THINK = re.compile(r"<think>.*?</think>\s*", re.S | re.I)


class LlmUnavailable(RuntimeError):
    pass


class NullBackend:
    """No model installed. Everything falls back to quoting the pack."""

    name = "none"
    model = None

    def available(self) -> bool:
        return False

    def generate(self, system: str, prompt: str, **kw: Any) -> str:
        raise LlmUnavailable("No local model is installed on this machine.")


class OllamaBackend:
    name = "ollama"

    def __init__(self, endpoint: str, model: str, timeout_s: int = 150,
                 num_ctx: int = 4096, num_thread: int | None = None,
                 max_tokens: int = 500) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.num_ctx = num_ctx
        self.num_thread = num_thread
        self.max_tokens = max_tokens
        self._checked: bool | None = None
        self._models: list[str] = []

    def _get(self, path: str) -> Any:
        req = urllib.request.Request(self.endpoint + path, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def available(self, recheck: bool = False) -> bool:
        if self._checked is not None and not recheck:
            return self._checked
        try:
            tags = self._get("/api/tags")
            self._models = [m.get("name", "") for m in tags.get("models", [])]
            self._checked = True
        except Exception:
            self._models = []
            self._checked = False
        return self._checked

    def installed_models(self) -> list[str]:
        self.available()
        return self._models

    def model_present(self) -> bool:
        names = self.installed_models()
        base = self.model.split(":")[0]
        return any(n == self.model or n.split(":")[0] == base for n in names)

    def generate(self, system: str, prompt: str, temperature: float = 0.2,
                 max_tokens: int | None = None) -> str:
        # Callers may ask for less than the machine's budget but never more:
        # on a constrained machine an over-long answer is how a lesson stalls.
        limit = self.max_tokens if max_tokens is None else min(max_tokens, self.max_tokens)
        options: dict[str, Any] = {
            "temperature": temperature,
            "num_predict": limit,
            "num_ctx": self.num_ctx,
            # Low top_p keeps a small model close to the supplied context,
            # which is the whole point here.
            "top_p": 0.85,
            "repeat_penalty": 1.05,
        }
        if self.num_thread:
            options["num_thread"] = self.num_thread
        payload = {
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": options,
        }
        req = urllib.request.Request(
            self.endpoint + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise LlmUnavailable(f"Local model server did not respond: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - includes the airlock
            raise LlmUnavailable(str(exc)) from exc
        return _THINK.sub("", body.get("response", "")).strip()


def get_backend(cfg: dict[str, Any]) -> NullBackend | OllamaBackend:
    llm = cfg.get("llm", {})
    if llm.get("backend") != "ollama":
        return NullBackend()
    return OllamaBackend(
        endpoint=llm.get("endpoint", "http://127.0.0.1:11434"),
        model=llm.get("model", "llama3.2:1b"),
        timeout_s=int(llm.get("timeout_s", 150)),
        num_ctx=int(llm.get("num_ctx", 4096)),
        num_thread=llm.get("num_thread") or None,
        max_tokens=int(llm.get("max_tokens", 500)),
    )
