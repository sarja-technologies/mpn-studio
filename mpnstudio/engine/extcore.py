"""A cognitive core on a server Studio does not run: any OpenAI-compatible chat endpoint.

ExternalCore has the surface the engine uses from `LlamaServer` (`alive`, `stream`, `post`, `props`, `tokenize`,
`apply_template`, `stop`), so the engine treats a core on llama.cpp, vLLM, Ollama or a hosted API the same way, and
the network is the same whichever core answers.

Servers differ in what they accept beyond the OpenAI request:
  * llama.cpp-only fields (`cache_prompt`, `timings_per_token`) are never sent to a server.
  * sampling extras (`top_k`, `min_p`, `repeat_penalty`) and `chat_template_kwargs` are sent; if the server answers HTTP
    400 or 422 the request is repeated with the plain OpenAI fields, and later requests stay plain.
  * `/tokenize` and `/apply-template` (llama.cpp, vLLM's `/tokenize`) give exact prompt sizes; without them sizes are
    estimated from the text, deliberately high, so a prompt is never packed past the context.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time

import httpx

from ..library import coreconf
from .llama import LlamaError

STANDARD = {"messages", "model", "max_tokens", "max_completion_tokens", "temperature", "top_p", "presence_penalty", "frequency_penalty", "seed", "stop",
            "tools", "tool_choice", "stream", "stream_options", "response_format", "n", "user"}
DROP = {"cache_prompt", "timings_per_token"}
CHARS_PER_TOKEN = 3.0          # below what common tokenizers average for English, so the estimate errs high


class ExternalCore:
    def __init__(self, conf: dict, api_key: str | None):
        self.name = "core"
        self.conf = conf
        self.base = coreconf.normalise_url(conf["base_url"])
        self.root = coreconf.root_of(self.base)
        self.model = conf["model_id"]
        self.key = api_key
        self.timeout = float(conf.get("timeout_s") or 120)
        self.max_out = int(conf["max_output_tokens"]) if conf.get("max_output_tokens") else None
        self.context = int(conf["context_tokens"]) if conf.get("context_tokens") else None
        self.strict = False
        self.load_s: float | None = None
        self.started_at: float | None = None
        self._up = False
        self._tok: str | None = None       # "llama" | "vllm" | "estimate"
        self._tpl: str | None = None       # "llama" | "plain"
        self._client = httpx.Client(timeout=httpx.Timeout(3600.0, connect=10.0, read=self.timeout))

    # ---------------------------------------------------------------- lifecycle
    @property
    def url(self) -> str:
        return self.root

    def start(self, timeout: float = 30) -> None:
        t0 = time.perf_counter()
        r = coreconf.test(self.base, self.model, self.key, timeout=min(timeout, self.timeout), probe=True)
        if not r["ok"]:
            raise RuntimeError(f"The core server did not accept the test question ({r['code']}): {r['message']}")
        if r.get("context_tokens") and not self.context:
            self.context = int(r["context_tokens"])
        self.warnings = r.get("warnings") or []
        self.load_s = round(time.perf_counter() - t0, 1)
        self.started_at = time.time()
        self._up = True

    def alive(self) -> bool:
        return self._up

    def stop(self) -> None:
        self._up = False
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    def tail(self, n: int = 0) -> str:
        return ""

    # ---------------------------------------------------------------- HTTP
    def _h(self) -> dict:
        return coreconf.headers(self.key)

    def _where(self, path: str) -> str:
        return self.base + path[3:] if path.startswith("/v1/") else self.root + path

    def _body(self, body: dict, strict: bool) -> dict:
        b = {k: v for k, v in body.items() if k not in DROP and v is not None}
        b["model"] = self.model
        if self.max_out:
            for k in ("max_tokens", "max_completion_tokens"):
                if k in b:
                    b[k] = min(int(b[k]), self.max_out)
        if strict or self.strict:
            b = {k: v for k, v in b.items() if k in STANDARD}
        return b

    def _fail(self, r: httpx.Response, text: str | None = None) -> LlamaError:
        t = text if text is not None else r.text
        if self.key:
            t = t.replace(self.key, "***")
        return LlamaError(r.status_code, t[:2000])

    def post(self, path: str, body: dict, timeout: float = 3600) -> dict:
        for strict in (False, True):
            try:
                r = self._client.post(self._where(path), headers=self._h(), json=self._body(body, strict) if path.startswith("/v1/") else body,
                                      timeout=httpx.Timeout(self.timeout if timeout >= 3600 else timeout, connect=10.0))
            except httpx.TimeoutException:
                self._up = False
                raise LlamaError(504, f"The core server did not answer within {self.timeout:.0f} s.")
            except httpx.HTTPError as e:
                self._up = False
                raise LlamaError(503, f"The core server stopped answering ({type(e).__name__}).")
            if r.status_code in (400, 422) and not strict and path.startswith("/v1/") and not self.strict and "context" not in r.text.lower():
                self.strict = True
                continue
            if r.status_code >= 400:
                raise self._fail(r)
            return r.json()
        raise LlamaError(500, "unreachable")

    def get(self, path: str, timeout: float = 30):
        r = self._client.get(self._where(path), headers=self._h(), timeout=timeout)
        if r.status_code >= 400:
            raise self._fail(r)
        return r.json()

    def stream(self, path: str, body: dict, timeout: float = 3600, cancel: threading.Event | None = None):
        """Yield the JSON objects of an SSE stream; closing early (cancel) stops generation on the server."""
        for strict in (False, True):
            try:
                with self._client.stream("POST", self._where(path), headers={**self._h(), "Accept": "text/event-stream"},
                                         json=self._body({**body, "stream": True}, strict)) as r:
                    if r.status_code in (400, 422) and not strict and not self.strict:
                        text = r.read().decode("utf-8", "replace")
                        if "context" not in text.lower():
                            self.strict = True
                            continue
                        raise self._fail(r, text)
                    if r.status_code >= 400:
                        raise self._fail(r, r.read().decode("utf-8", "replace"))
                    for line in r.iter_lines():
                        if cancel is not None and cancel.is_set():
                            return
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        yield json.loads(data)
                    return
            except httpx.TimeoutException:
                self._up = False
                raise LlamaError(504, f"The core server went quiet for {self.timeout:.0f} s.")
            except httpx.HTTPError as e:
                self._up = False
                raise LlamaError(503, f"The core server stopped answering ({type(e).__name__}).")

    # ---------------------------------------------------------------- what the engine asks of a core
    def props(self) -> dict:
        out: dict = {"build_info": "external core server"}
        try:
            p = self._client.get(self.root + "/props", headers=self._h(), timeout=5)
            if p.status_code == 200:
                out.update(p.json())
        except (httpx.HTTPError, ValueError):
            pass
        n = (out.get("default_generation_settings") or {}).get("n_ctx") or self.context
        if n:
            out.setdefault("default_generation_settings", {})["n_ctx"] = int(n)
        out.setdefault("modalities", {"vision": False})
        return out

    def tokenize(self, text: str, special: bool = True) -> list[int]:
        if self._tok in (None, "llama"):
            try:
                r = self._client.post(self.root + "/tokenize", headers=self._h(), json={"content": text, "add_special": special, "parse_special": True}, timeout=30)
                if r.status_code == 200 and "tokens" in r.json():
                    self._tok = "llama"
                    return r.json()["tokens"]
            except (httpx.HTTPError, ValueError):
                pass
        if self._tok in (None, "vllm"):
            try:
                r = self._client.post(self.root + "/tokenize", headers=self._h(), json={"model": self.model, "prompt": text, "add_special_tokens": special}, timeout=30)
                if r.status_code == 200 and "tokens" in r.json():
                    self._tok = "vllm"
                    return r.json()["tokens"]
            except (httpx.HTTPError, ValueError):
                pass
        self._tok = "estimate"
        return [0] * max(1, math.ceil(len(text) / CHARS_PER_TOKEN))

    def apply_template(self, messages: list[dict], **kw) -> str:
        if self._tpl in (None, "llama"):
            try:
                r = self._client.post(self.root + "/apply-template", headers=self._h(), json={"messages": messages, **kw}, timeout=30)
                if r.status_code == 200 and "prompt" in r.json():
                    self._tpl = "llama"
                    return r.json()["prompt"]
            except (httpx.HTTPError, ValueError):
                pass
        self._tpl = "plain"
        return "".join(f"<|{m['role']}|>\n{m['content'] if isinstance(m.get('content'), str) else json.dumps(m.get('content'))}\n" for m in messages) + "<|assistant|>\n"
