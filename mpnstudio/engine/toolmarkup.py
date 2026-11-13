"""Tool-call markup that reaches the content stream.

llama-server turns the core's tool calls into structured `tool_calls` when its template parser recognises them. Some
cores also write calls in a form the parser did not recognise, or keep writing them when no tools are offered; that
markup would otherwise reach the user as answer text. `MarkupGuard` holds it back from the stream, and `parse_calls`
reads the three forms seen from the candidate cores:

  <tool_call>{"name": "mpn_search", "arguments": {"query": "..."}}</tool_call>                     (JSON, Hermes/Qwen3)
  <tool_call>mpn_search<arg_key>query</arg_key><arg_value>...</arg_value></tool_call>             (GLM style)
  <tool_call><function=mpn_search><parameter=query>...</parameter></function></tool_call>          (Qwen3.5 XML)

`normalize_arguments` makes a call's `arguments` a JSON object string. llama-server renders earlier assistant turns
through `json::parse` of their arguments, so a call with broken arguments, sent back in the conversation, fails the
next round with HTTP 500 ("Failed to parse tool call arguments as JSON").
"""
from __future__ import annotations

import json
import re
import uuid

MARKERS = ("<tool_call>", "<function=")

_BLOCK = re.compile(r"<tool_call>(.*?)(?:</tool_call>|$)", re.S)
_FUNC = re.compile(r"<function=([^>\s]+)>(.*?)(?:</function>|$)", re.S)
_PARAM = re.compile(r"<parameter=([^>\s]+)>(.*?)(?:</parameter>|(?=<parameter=)|$)", re.S)
_ARG = re.compile(r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)(?:</arg_value>|$)", re.S)


class MarkupGuard:
    """Streaming filter: passes text through until a tool-call marker starts, then holds everything after it."""

    def __init__(self):
        self.buf = ""
        self.held = ""

    def feed(self, text: str) -> str:
        if self.held:
            self.held += text
            return ""
        self.buf += text
        hits = [i for i in (self.buf.find(m) for m in MARKERS) if i >= 0]
        if hits:
            i = min(hits)
            out, self.held, self.buf = self.buf[:i], self.buf[i:], ""
            return out
        keep = 0     # keep a tail that could be the start of a marker split across chunks
        for m in MARKERS:
            for k in range(min(len(m) - 1, len(self.buf)), 0, -1):
                if self.buf.endswith(m[:k]):
                    keep = max(keep, k)
                    break
        out = self.buf[: len(self.buf) - keep]
        self.buf = self.buf[len(self.buf) - keep:]
        return out

    def finish(self) -> tuple[str, str]:
        """Returns (text still to emit, held markup)."""
        out, self.buf = self.buf, ""
        return out, self.held


def _value(v: str):
    v = v.strip()
    try:
        return json.loads(v)
    except (json.JSONDecodeError, ValueError):
        return v


def _call(name: str, args: dict) -> dict:
    return {"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
            "function": {"name": name.strip(), "arguments": json.dumps(args, ensure_ascii=False)}}


def _parse_one(body: str) -> dict | None:
    body = body.strip()
    m = _FUNC.search(body)
    if m:
        return _call(m.group(1), {k.strip(): _value(v) for k, v in _PARAM.findall(m.group(2))})
    if body.startswith("{"):
        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            d = None
        if isinstance(d, dict) and isinstance(d.get("name"), str):
            a = d.get("arguments", d.get("parameters", {}))
            if isinstance(a, str):
                a = _value(a)
            return _call(d["name"], a if isinstance(a, dict) else {})
        return None
    if "<arg_key>" in body or re.fullmatch(r"[A-Za-z_][\w.-]*", body):
        name = body.split("<arg_key>", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][\w.-]*", name):
            return _call(name, {k.strip(): _value(v) for k, v in _ARG.findall(body)})
    return None


def parse_calls(text: str) -> list[dict]:
    """Tool calls written as markup in `text`, in order. Unrecognised blocks are skipped."""
    calls = []
    blocks = _BLOCK.findall(text) if "<tool_call>" in text else [m.group(0) for m in _FUNC.finditer(text)]
    for b in blocks:
        c = _parse_one(b)
        if c:
            calls.append(c)
    return calls


def normalize_arguments(call: dict) -> bool:
    """Make call.function.arguments a JSON object string in place. Returns False if the original was not valid JSON."""
    f = call.setdefault("function", {})
    a = f.get("arguments")
    ok = True
    if isinstance(a, dict):
        obj = a
    else:
        try:
            obj = json.loads(a or "{}")
        except (json.JSONDecodeError, TypeError):
            obj, ok = {}, False
        if not isinstance(obj, dict):
            obj, ok = {}, False
    f["arguments"] = json.dumps(obj, ensure_ascii=False)
    return ok
