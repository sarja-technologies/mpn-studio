"""The MPN runtime's local HTTP API: an OpenAI-compatible chat endpoint plus the MPN endpoints (status, route evidence,
conversations, nodes, procedures).

Binds 127.0.0.1 by default. Listening on any other address requires an API key (--api-key, MPN_API_KEY or
YAOB_API_KEY); every endpoint except /health then needs `Authorization: Bearer <key>`.

Models served on /v1/models, where <id> is the model's id from its manifest (yaob-c4b-n10k for the Yaob release):
  <id>            core + MPN (routing, node evidence, procedures, MPN tools)   default
  <id>-core       the core alone (no node is read)
  <id>-retrieval  core + ordinary retrieval (baseline)
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import signal
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from mpnstudio.api import ops
from mpnstudio.runtime import procedures
from mpnstudio.store.db import iid

from .. import __version__
from .convstore import ConvStore
from .engine import Engine

MODEL = "yaob-c4b-n10k"       # the Yaob release; a model built in MPN Studio carries its own id


def modes_for(model_id: str) -> dict[str, str]:
    return {model_id: "yaob", model_id + "-core": "core", model_id + "-retrieval": "retrieval"}


MODES = modes_for(MODEL)
MAX_BODY = 64 << 20         # images arrive base64-encoded in the request body


class HTTPError(Exception):
    def __init__(self, status: int, message: str, kind: str = "invalid_request_error"):
        super().__init__(message)
        self.status, self.message, self.kind = status, message, kind


def _think_of(body: dict) -> bool | None:
    """Thinking switch: `think` (Yaob), chat_template_kwargs.enable_thinking (llama.cpp/vLLM), or reasoning_effort (OpenAI)."""
    if "think" in body and body["think"] is not None:
        return bool(body["think"])
    k = body.get("chat_template_kwargs") or {}
    if "enable_thinking" in k:
        return bool(k["enable_thinking"])
    eff = body.get("reasoning_effort")
    if eff is not None:
        return str(eff).lower() not in ("none", "minimal", "off", "false")
    return None


def _budget_of(body: dict) -> dict:
    b = dict(body.get("budget") or {})
    mt = body.get("max_completion_tokens", body.get("max_tokens"))
    if mt is not None:
        b["requested_output_tokens"] = int(mt)
    s = {k: body[k] for k in ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty", "repeat_penalty") if body.get(k) is not None}
    if body.get("stop"):
        s["stop"] = body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
    if s:
        b["sampling"] = s
    if body.get("seed") is not None:
        b["seed"] = int(body["seed"])
    return b


class App:
    def __init__(self, engine: Engine, api_key: str | None, loopback_only: bool = True):
        self.engine, self.api_key, self.loopback_only = engine, api_key, loopback_only
        self.convs = ConvStore(engine.data / "conversations.sqlite")
        self.started = time.time()
        self.model = engine.model_id
        self.modes = modes_for(self.model)

    # ---------------------------------------------------------------- chat completions
    def chat(self, body: dict, h: "Handler"):
        model = body.get("model") or self.model
        if model not in self.modes:
            raise HTTPError(404, f"The model '{model}' does not exist. Available: {', '.join(self.modes)}.", "model_not_found")
        msgs = body.get("messages")
        if not isinstance(msgs, list) or not msgs:
            raise HTTPError(400, "messages must be a non-empty list.")
        if body.get("n", 1) != 1:
            raise HTTPError(400, "Only n=1 is supported.")
        tools = body.get("tools") or []
        if body.get("tool_choice") == "none":
            tools = []
        rid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())
        events = self.engine.run(msgs, mode=self.modes[model], think=_think_of(body), budget=_budget_of(body), client_tools=tools, request_id=rid,
                                 rev=body.get("rev"))
        if body.get("stream"):
            return self._chat_stream(events, rid, created, model, body, h)
        text, reasoning, route, validation, done = "", [], None, None, None
        for ev, d in events:
            if ev == "error":
                raise HTTPError(d.get("status", 500), d["error"], "server_error" if d.get("status", 500) >= 500 else "invalid_request_error")
            if ev == "reasoning":
                reasoning.append(d["text"])
            elif ev == "route":
                route = d
            elif ev == "validation":
                validation = d
            elif ev == "done":
                done = d
        msg = {"role": "assistant", "content": done["text"] if not done["tool_calls"] or done["text"] else None}
        if done.get("reasoning"):
            msg["reasoning_content"] = done["reasoning"]
        if done["tool_calls"]:
            msg["tool_calls"] = done["tool_calls"]
        u = done["usage"]
        return h.send_json(200, {"id": rid, "object": "chat.completion", "created": created, "model": model,
                                 "choices": [{"index": 0, "message": msg, "finish_reason": self._finish(done)}],
                                 "usage": {"prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0),
                                           "total_tokens": u.get("total_tokens", 0)},
                                 "yaob": {"route": route, "validation": validation, "timing": done["timing"], "tool_rounds": u.get("tool_rounds")}})

    @staticmethod
    def _finish(done: dict) -> str:
        if done.get("cancelled"):
            return "stop"
        if done.get("tool_calls"):
            return "tool_calls"
        return "length" if done.get("finish") == "length" else "stop"

    def _chat_stream(self, events, rid, created, model, body, h):
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        started = False

        def chunk(delta: dict, finish=None, extra=None):
            c = {"id": rid, "object": "chat.completion.chunk", "created": created, "model": model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if extra:
                c.update(extra)
            return "data: " + json.dumps(c, ensure_ascii=False) + "\n\n"

        try:
            for ev, d in events:
                if ev == "error":
                    if not started:
                        raise HTTPError(d.get("status", 500), d["error"], "server_error")
                    h.write_raw("data: " + json.dumps({"error": {"message": d["error"], "type": "server_error"}}) + "\n\n")
                    break
                if not started:
                    h.start_sse()
                    h.write_raw(chunk({"role": "assistant", "content": ""}))
                    started = True
                if ev == "token":
                    h.write_raw(chunk({"content": d["text"]}))
                elif ev == "reasoning":
                    h.write_raw(chunk({"reasoning_content": d["text"]}))
                elif ev == "route":
                    h.write_raw(chunk({}, extra={"yaob": {"route": d}}))
                elif ev == "validation":
                    h.write_raw(chunk({}, extra={"yaob": {"validation": d}}))
                elif ev == "done":
                    if d["tool_calls"]:
                        h.write_raw(chunk({"tool_calls": [{"index": i, **tc} for i, tc in enumerate(d["tool_calls"])]}))
                    h.write_raw(chunk({}, self._finish(d), extra={"yaob": {"timing": d["timing"]}}))
                    if include_usage:
                        u = d["usage"]
                        h.write_raw("data: " + json.dumps({"id": rid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [],
                                                           "usage": {"prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0),
                                                                     "total_tokens": u.get("total_tokens", 0)}}) + "\n\n")
            if started:
                h.write_raw("data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            self.engine.cancel(rid)
            events.close()

    # ---------------------------------------------------------------- /run (Yaob event stream, used by the evaluations)
    def run(self, body: dict, h: "Handler", conv_id: str | None = None):
        mode = body.get("mode", "yaob")
        if mode not in ("yaob", "core", "retrieval"):
            raise HTTPError(400, "mode must be yaob, core or retrieval")
        rid = uuid.uuid4().hex[:12]
        events = self.engine.run(body.get("messages") or [], mode=mode, rev=body.get("rev"), think=_think_of(body), budget=_budget_of(body),
                                 client_tools=body.get("tools") or [], request_id=rid)
        h.start_sse()
        final, route, validation = None, None, None
        try:
            for ev, d in events:
                if ev == "route":
                    route = d
                elif ev == "validation":
                    validation = d
                elif ev == "done":
                    final = d
                h.write_raw(f"event: {ev}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n")
        except (BrokenPipeError, ConnectionResetError):
            self.engine.cancel(rid)
            events.close()
            return
        if conv_id and final and not final.get("cancelled"):
            self.convs.add(conv_id, "assistant", final["text"], final.get("reasoning") or None,
                           {"route": route, "validation": validation, "timing": final["timing"], "usage": final["usage"]})

    # ---------------------------------------------------------------- routing table
    def route(self, method: str, path: str, query: dict, body: dict | None, h: "Handler"):
        e = self.engine
        parts = [p for p in path.split("/") if p]
        if method == "GET" and path == "/health":
            ok = bool(e.core and e.core.alive())
            return h.send_json(200 if ok else 503, {"status": "ok" if ok else ("loading" if e.loading else "unavailable")})
        if method == "GET" and path == "/v1/models":
            return h.send_json(200, {"object": "list", "data": [{"id": m, "object": "model", "created": int(self.started), "owned_by": e.manifest.get("owned_by", "fylume"),
                                                                       **({"name": e.model_name} if m == self.model else {})} for m in self.modes]})
        if method == "GET" and len(parts) == 3 and parts[:2] == ["v1", "models"]:
            if parts[2] not in self.modes:
                raise HTTPError(404, f"The model '{parts[2]}' does not exist.", "model_not_found")
            return h.send_json(200, {"id": parts[2], "object": "model", "created": int(self.started), "owned_by": e.manifest.get("owned_by", "fylume")})
        if method == "POST" and path == "/v1/chat/completions":
            return self.chat(body or {}, h)
        if method == "GET" and path == "/status":
            return h.send_json(200, {"version": __version__, "model": self.model, **e.status()})
        if method == "POST" and path == "/run":
            return self.run(body or {}, h)
        if method == "POST" and path == "/cancel":
            return h.send_json(200, {"cancelled": e.cancel(str((body or {}).get("request_id", "")))})
        if method == "POST" and path == "/evict":
            return h.send_json(200, {"evicted": e.ck.shards.evict() if e.ck else 0})
        if method == "POST" and path == "/api/load":
            return h.send_json(200, e.load())
        if method == "POST" and path == "/api/unload":
            return h.send_json(200, e.unload())
        if path == "/api/budget":
            if method == "GET":
                return h.send_json(200, e.budget)
            if method in ("POST", "PATCH"):
                try:
                    return h.send_json(200, e.set_budget(body or {}))
                except ValueError as x:
                    raise HTTPError(400, str(x))
        # conversations
        if parts[:2] == ["api", "conversations"]:
            if len(parts) == 2 and method == "GET":
                return h.send_json(200, {"conversations": self.convs.list()})
            if len(parts) == 2 and method == "POST":
                b = body or {}
                return h.send_json(201, self.convs.create(b.get("title") or "New conversation", b.get("mode", "yaob"), b.get("think")))
            cid = parts[2] if len(parts) > 2 else ""
            conv = self.convs.get(cid)
            if conv is None:
                raise HTTPError(404, "No such conversation.")
            if len(parts) == 3:
                if method == "GET":
                    return h.send_json(200, conv)
                if method == "PATCH":
                    return h.send_json(200, self.convs.update(cid, **(body or {})))
                if method == "DELETE":
                    return h.send_json(200, {"deleted": self.convs.delete(cid)})
            if len(parts) == 4 and parts[3] == "messages" and method == "POST":
                b = body or {}
                content = str(b.get("content", "")).strip()
                if not content:
                    raise HTTPError(400, "content is required")
                self.convs.add(cid, "user", content)
                hist = [{"role": m["role"], "content": m["content"]} for m in self.convs.get(cid)["messages"]]
                think = b.get("think", conv["think"])
                return self.run({"messages": hist, "mode": b.get("mode", conv["mode"]), "think": think, "budget": b.get("budget")}, h, conv_id=cid)
        # network
        if path == "/api/mpns" and method == "GET":
            return h.send_json(200, {"mpns": e.store.mpns()})
        if len(parts) == 4 and parts[:2] == ["api", "mpns"] and method == "POST" and parts[3] in ("enable", "disable"):
            try:
                return h.send_json(200, ops.set_mpn(e.store, iid(parts[2]), parts[3], actor="yaob-api"))
            except ops.OpError as x:
                raise HTTPError(x.status, str(x))
        if path == "/api/nodes/search" and method == "GET":
            q = (query.get("q") or [""])[0]
            limit = min(int((query.get("limit") or ["20"])[0]), 100)
            kind = (query.get("mode") or ["text"])[0]
            if kind == "semantic":
                return h.send_json(200, {"results": e.semantic(q, limit)})
            return h.send_json(200, {"results": e.store.search_lexical(q, limit=limit, any_terms=True)})
        if parts[:2] == ["api", "nodes"]:
            try:
                if len(parts) == 2 and method == "POST":
                    r = ops.create_node(e.store, self._node_payload(body or {}), actor="yaob-api")
                    r["indexed"] = e.reindex()
                    return h.send_json(201, r)
                nid = iid(parts[2]) if len(parts) > 2 else 0
                if len(parts) == 3 and method == "GET":
                    return h.send_json(200, ops.node_detail(e.store, nid))
                if len(parts) == 3 and method == "PATCH":
                    r = ops.update_node(e.store, nid, self._node_payload(body or {}, nid), actor="yaob-api")
                    r["indexed"] = e.reindex()
                    return h.send_json(200, r)
                if len(parts) == 3 and method == "DELETE":
                    return h.send_json(200, ops.delete_node(e.store, nid, actor="yaob-api"))
                if len(parts) == 4 and parts[3] == "restore" and method == "POST":
                    return h.send_json(200, ops.restore_node(e.store, nid, actor="yaob-api"))
            except ops.OpError as x:
                raise HTTPError(x.status, str(x) + (f" {json.dumps(x.detail)}" if x.detail else ""))
            except (KeyError, ValueError) as x:
                raise HTTPError(400, str(x))
        if path == "/api/procedures" and method == "GET":
            return h.send_json(200, {"procedures": [{"impl": k, "title": p.title, "summary": p.summary, "params": p.params} for k, p in procedures.REGISTRY.items()]})
        if len(parts) == 4 and parts[:2] == ["api", "procedures"] and parts[3] == "run" and method == "POST":
            if parts[2] not in procedures.REGISTRY:
                raise HTTPError(404, "No such procedure.")
            return h.send_json(200, procedures.run(parts[2], (body or {}).get("args") or {}))
        raise HTTPError(404, f"No endpoint {method} {path}")

    def _node_payload(self, b: dict, existing: int | None = None) -> dict:
        """Accept the simple form {title, summary, statements: [str], facts: [{property, value}], mpn} as well as full cells."""
        p = {k: b[k] for k in ("title", "summary", "aliases", "type", "cells", "schema", "sources", "memberships", "status") if k in b}
        if "statements" in b or "facts" in b:
            cur = self.engine.store.get_node(existing) if existing else None
            cells = dict((cur or {}).get("cells") or {"knowledge": {}, "thinking": {}, "reasoning": {}})
            k = dict(cells.get("knowledge") or {})
            if "statements" in b:
                k["statements"] = [{"text": str(s)} if not isinstance(s, dict) else s for s in b["statements"]]
            if "facts" in b:
                k["facts"] = b["facts"]
            cells["knowledge"] = k
            p["cells"] = cells
        if "mpn" in b and not existing:
            m = b["mpn"]
            mm = self.engine.store.mpn_by_slug(m) if isinstance(m, str) and not m.isdigit() else self.engine.store.mpn(iid(m))
            if mm is None:
                raise HTTPError(400, f"No MPN {m!r}.")
            p["memberships"] = [{"mpn_id": mm["id"]}]
        return p


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MPN-Runtime/" + __version__
    app: App = None

    def log_message(self, fmt, *args):
        if os.environ.get("YAOB_ACCESS_LOG"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # responses
    def send_json(self, status: int, obj) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def start_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def write_raw(self, s: str) -> None:
        self.wfile.write(s.encode())
        self.wfile.flush()

    def _error(self, status: int, message: str, kind: str = "invalid_request_error") -> None:
        try:
            self.send_json(status, {"error": {"message": message, "type": kind, "code": status}})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authorised(self, path: str) -> bool:
        key = self.app.api_key
        if not key or path == "/health":
            return True
        got = self.headers.get("Authorization", "")
        got = got[7:].strip() if got.lower().startswith("bearer ") else self.headers.get("X-API-Key", "")
        return hmac.compare_digest(got.encode(), key.encode())

    def _handle(self, method: str):
        u = urlparse(self.path)
        try:
            if self.app.loopback_only:
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
                if host not in ("127.0.0.1", "localhost", "::1", ""):
                    raise HTTPError(403, "This server only answers requests addressed to localhost.", "permission_error")
            if method in ("POST", "PATCH", "PUT") and int(self.headers.get("Content-Length") or 0) and \
                    not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                raise HTTPError(415, "Send the body as JSON with Content-Type: application/json.")
            if not self._authorised(u.path):
                raise HTTPError(401, "A valid API key is required (Authorization: Bearer <key>).", "authentication_error")
            body = None
            if method in ("POST", "PATCH", "PUT"):
                n = int(self.headers.get("Content-Length") or 0)
                if n > MAX_BODY:
                    raise HTTPError(413, f"The request body is larger than {MAX_BODY >> 20} MiB.")
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw else {}
                except json.JSONDecodeError as x:
                    raise HTTPError(400, f"The body is not valid JSON: {x}")
                if not isinstance(body, dict):
                    raise HTTPError(400, "The body must be a JSON object.")
            self.app.route(method, u.path.rstrip("/") or "/", parse_qs(u.query), body, self)
        except HTTPError as x:
            self._error(x.status, x.message, x.kind)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as x:
            traceback.print_exc()
            self._error(500, f"{type(x).__name__}: {x}", "server_error")
        finally:
            st = self.app.engine.store     # each request runs in its own thread with its own connection: close it
            c = getattr(st._local, "c", None) if st else None
            if c is not None:
                try:
                    c.close()
                finally:
                    st._local.c = None

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")


def is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def serve(root: Path, data: Path, host: str = "127.0.0.1", port: int = 8842, api_key: str | None = None, device: str = "auto",
          context: int | None = None, threads: int | None = None, parallel: int | None = None) -> None:
    if not is_loopback(host) and not api_key:
        raise SystemExit(f"Refusing to listen on {host} without an API key. Pass --api-key (or set MPN_API_KEY), or keep the default 127.0.0.1.")
    data.mkdir(parents=True, exist_ok=True)
    eng = Engine(root, data, device=device, context=context, threads=threads)
    if parallel:
        eng.budget["concurrent_requests"] = int(parallel)
        eng.sem = threading.BoundedSemaphore(int(parallel))
    Handler.app = App(eng, api_key, loopback_only=is_loopback(host))
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True

    def stop(*_):
        threading.Thread(target=httpd.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"{eng.model_name}: MPN runtime {__version__}, API on http://{host}:{port}  (loading the model ...)", flush=True)
    loader = threading.Thread(target=lambda: _load(eng), daemon=True)
    loader.start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        eng.unload()
        print(f"{eng.model_name} stopped.", flush=True)


def _load(eng: Engine) -> None:
    try:
        info = eng.load(log=lambda m: print(m, flush=True))
        print(f"Ready in {info['load_s']} s on {info['variant']}.", flush=True)
    except Exception as x:
        eng.last_error = str(x)
        print(f"Loading failed: {x}", file=sys.stderr, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="yaob serve")
    ap.add_argument("--root", default=os.environ.get("YAOB_ROOT", "."))
    ap.add_argument("--data", default=os.environ.get("YAOB_DATA"))
    ap.add_argument("--host", default=os.environ.get("YAOB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("YAOB_PORT", "8842")))
    ap.add_argument("--api-key", default=os.environ.get("MPN_API_KEY") or os.environ.get("YAOB_API_KEY"))
    ap.add_argument("--device", default=os.environ.get("YAOB_DEVICE", "auto"), choices=["auto", "cuda", "vulkan", "cpu"])
    ap.add_argument("--context", type=int, default=int(os.environ["YAOB_CONTEXT"]) if os.environ.get("YAOB_CONTEXT") else None)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--parallel", type=int, default=None, help="concurrent requests (each gets the full context)")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    data = Path(a.data).resolve() if a.data else root / "data"
    serve(root, data, a.host, a.port, a.api_key, a.device, a.context, a.threads, a.parallel)


if __name__ == "__main__":
    main()
