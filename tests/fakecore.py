"""A fake OpenAI-compatible server for tests: lists models, answers chat completions (streamed or not), checks an API key,
and can imitate the ways real servers differ (a model-id check like vLLM, a request that rejects extra fields, a slow answer)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeCore:
    def __init__(self, name="core-A", model="fake-model", key=None, strict_ids=False, reject_extras=False, delay=0.0, n_ctx=None, context_error=False):
        self.name, self.model, self.key, self.strict_ids, self.reject_extras, self.delay = name, model, key, strict_ids, reject_extras, delay
        self.n_ctx, self.context_error = n_ctx, context_error
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                b = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _auth(self):
                if outer.key and self.headers.get("Authorization") != f"Bearer {outer.key}":
                    self._send(401, {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}})
                    return False
                return True

            def do_GET(self):
                p = self.path.split("?")[0]
                if p in ("/v1/models", "/models"):
                    if not self._auth():
                        return
                    return self._send(200, {"object": "list", "data": [{"id": outer.model, "object": "model", **({"max_model_len": outer.n_ctx} if outer.n_ctx else {})}]})
                if p == "/props" and outer.n_ctx:
                    return self._send(200, {"default_generation_settings": {"n_ctx": outer.n_ctx}})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                if self.path != "/v1/chat/completions":
                    return self._send(404, {"error": "not found"})
                if not self._auth():
                    return
                if outer.strict_ids and body.get("model") != outer.model:
                    return self._send(404, {"error": {"message": f"The model `{body.get('model')}` does not exist.", "type": "NotFoundError", "code": 404}})
                if outer.reject_extras and any(k in body for k in ("chat_template_kwargs", "top_k", "min_p", "repeat_penalty")):
                    return self._send(400, {"error": {"message": "Unrecognized request argument supplied: top_k"}})
                if outer.context_error:
                    return self._send(400, {"error": {"message": "This model's maximum context length is 100 tokens. Your request exceeds the context window."}})
                time.sleep(outer.delay)
                text = f"{outer.name}: ready"
                if any("Knowledge nodes" in str(m.get("content")) for m in body.get("messages", [])):
                    text = f"{outer.name} answers from the network."
                usage = {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18}
                if not body.get("stream"):
                    return self._send(200, {"id": "x", "object": "chat.completion", "model": outer.model, "usage": usage,
                                            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]})
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()

                def chunk(obj):
                    d = ("data: " + (obj if isinstance(obj, str) else json.dumps(obj)) + "\n\n").encode()
                    self.wfile.write(f"{len(d):x}\r\n".encode() + d + b"\r\n")
                for w in text.split(" "):
                    chunk({"choices": [{"index": 0, "delta": {"content": w + " "}}]})
                chunk({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage})
                chunk("[DONE]")
                self.wfile.write(b"0\r\n\r\n")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
