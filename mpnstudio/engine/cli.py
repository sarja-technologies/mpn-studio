"""yaob: command line for the local Yaob backend.

  yaob serve [--host --port --api-key --device --context]   run the API (foreground)
  yaob chat  [--conversation ID] [--think] [--mode M]     interactive chat; conversations are saved
  yaob ask "question" [--think] [--mode M] [--json]       one answer, then exit
  yaob status                                                 what is loaded, device, memory, network
  yaob conversations                                          list saved conversations

chat/ask use the API at --url (default http://127.0.0.1:8842). If nothing answers there, they start a private server
for the duration of the command and stop it afterwards.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DIM, BOLD, RESET = ("\033[2m", "\033[1m", "\033[0m") if sys.stdout.isatty() else ("", "", "")


class Client:
    def __init__(self, url: str, key: str | None):
        self.url, self.key = url.rstrip("/"), key

    def _req(self, method: str, path: str, body=None, timeout=60):
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = "Bearer " + self.key
        return urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)

    def get(self, path: str, timeout=30):
        return json.loads(urllib.request.urlopen(self._req("GET", path), timeout=timeout).read())

    def post(self, path: str, body: dict, timeout=60):
        return json.loads(urllib.request.urlopen(self._req("POST", path, body), timeout=timeout).read())

    def sse(self, path: str, body: dict):
        resp = urllib.request.urlopen(self._req("POST", path, body), timeout=3600)
        ev, data = None, []
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and ev:
                yield ev, json.loads("\n".join(data) or "{}")
                ev, data = None, []

    def health(self) -> str | None:
        try:
            return self.get("/health", timeout=3).get("status")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read()).get("status")
            except Exception:
                return "error"
        except Exception:
            return None


def ensure_server(args) -> tuple[Client, subprocess.Popen | None]:
    c = Client(args.url, args.api_key)
    st = c.health()
    proc = None
    if st is None:
        from urllib.parse import urlparse
        u = urlparse(args.url)
        root = Path(os.environ.get("YAOB_ROOT", ".")).resolve()
        print(f"{DIM}Starting Yaob (this loads the model; it can take a minute) ...{RESET}", file=sys.stderr)
        logdir = Path(os.environ.get("YAOB_DATA", root / "data")) / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        log = open(logdir / "server.log", "ab")
        proc = subprocess.Popen([sys.executable, "-m", "yaobnext.backend.server", "--root", str(root), "--host", u.hostname or "127.0.0.1",
                                 "--port", str(u.port or 8842), "--device", args.device], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    t0 = time.time()
    while True:
        st = c.health()
        if st == "ok":
            return c, proc
        if proc is not None and proc.poll() is not None:
            raise SystemExit("The Yaob server exited while loading; see data/logs/server.log and data/logs/core.log.")
        if st == "unavailable" and proc is None:
            try:
                err = c.get("/status").get("last_error")
            except Exception:
                err = None
            raise SystemExit(f"The Yaob server at {args.url} has no model loaded" + (f": {err}" if err else "."))
        if time.time() - t0 > 900:
            raise SystemExit("Yaob did not become ready in 15 minutes.")
        time.sleep(1)


def stop_server(proc):
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def show_answer(events, show_thinking: bool, out=sys.stdout) -> dict:
    final, route, validation, in_reason = {}, None, None, False
    for ev, d in events:
        if ev == "reasoning" and show_thinking:
            if not in_reason:
                out.write(DIM + "thinking: ")
                in_reason = True
            out.write(d["text"])
            out.flush()
        elif ev == "token":
            if in_reason:
                out.write(RESET + "\n\n")
                in_reason = False
            out.write(d["text"])
            out.flush()
        elif ev == "tool":
            out.write(f"{DIM}[{d['name']} {d['arguments']} -> {d['result_summary'][:160]}]{RESET}\n")
        elif ev == "route":
            route = d
        elif ev == "validation":
            validation = d
        elif ev == "error":
            out.write(f"\nError: {d['error']}\n")
            return {"error": d["error"]}
        elif ev == "done":
            final = d
    if in_reason:
        out.write(RESET)
    out.write("\n")
    cited = [c for c in (validation or {}).get("citations", []) if c["cited"]]
    if cited:
        out.write(DIM + "Sources: " + "; ".join(f"[{c['n']}] {c['title']}" for c in cited) + RESET + "\n")
    t = final.get("timing") or {}
    if final:
        out.write(f"{DIM}{final['usage'].get('completion_tokens', 0)} tokens, {t.get('total_ms', 0) / 1000:.1f} s"
                  + (f", {t['tokens_per_s']} tok/s" if t.get("tokens_per_s") else "") + RESET + "\n")
    return {"final": final, "route": route, "validation": validation}


def cmd_chat(args):
    c, proc = ensure_server(args)
    try:
        think = args.think
        conv = c.get(f"/api/conversations/{args.conversation}") if args.conversation else c.post("/api/conversations", {"mode": args.mode, "think": think})
        st = c.get("/status")
        core = st.get("core", {})
        print(f"{BOLD}{(st.get('model') or {}).get('name') or 'MPN model'}{RESET} {DIM}core {core.get('name')} {core.get('quantization') or ''} on {st['device']['variant']}; "
              f"{st.get('network', {}).get('nodes', '?')} nodes. Conversation {conv['id']}.{RESET}")
        print(f"{DIM}/think on|off  /mode yaob|core|retrieval  /route  /new  /list  /open ID  /status  /quit{RESET}")
        for m in conv.get("messages", []):
            print(f"{BOLD}{m['role']}>{RESET} {m['content']}")
        last = None
        mode = conv.get("mode") or args.mode
        if conv.get("think") is not None:
            think = conv["think"]
        while True:
            try:
                line = input(f"{BOLD}you>{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.startswith("/"):
                cmd, _, arg = line[1:].partition(" ")
                if cmd in ("quit", "exit", "q"):
                    break
                if cmd == "think":
                    think = arg.strip().lower() not in ("off", "0", "no", "false")
                    urllib.request.urlopen(c._req("PATCH", f"/api/conversations/{conv['id']}", {"think": think}), timeout=10).read()
                    print(f"thinking {'on' if think else 'off'}")
                elif cmd == "mode":
                    if arg.strip() in ("yaob", "core", "retrieval"):
                        mode = arg.strip()
                        urllib.request.urlopen(c._req("PATCH", f"/api/conversations/{conv['id']}", {"mode": mode}), timeout=10).read()
                    print(f"mode {mode}")
                elif cmd == "route":
                    r = (last or {}).get("route") or {}
                    for m in r.get("mpns", []):
                        print(f"  MPN {m['name']:<28} p={m['score']:.3f}{' active' if m['active'] else ''}")
                    for n in r.get("nodes", []):
                        print(f"  node {n['title'][:48]:<48} gate={n['gate']:.2f} via {n['via']} ({n['mpn']})")
                    for s in r.get("searches", []):
                        print(f"  {s['tool']} {s.get('query') or s.get('relation')}: {', '.join(s['found']) or 'nothing'}")
                elif cmd == "new":
                    conv = c.post("/api/conversations", {"mode": mode, "think": think})
                    print(f"conversation {conv['id']}")
                elif cmd == "list":
                    for x in c.get("/api/conversations")["conversations"][:20]:
                        print(f"  {x['id']}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(x['updated']))}  {x['messages']:>3} msgs  {x['title']}")
                elif cmd == "open":
                    conv = c.get(f"/api/conversations/{arg.strip()}")
                    for m in conv["messages"]:
                        print(f"{BOLD}{m['role']}>{RESET} {m['content']}")
                elif cmd == "status":
                    print(json.dumps(c.get("/status"), indent=1)[:4000])
                else:
                    print("unknown command")
                continue
            sys.stdout.write(f"{BOLD}model>{RESET} ")
            try:
                last = show_answer(c.sse(f"/api/conversations/{conv['id']}/messages", {"content": line, "think": think, "mode": mode}), args.show_thinking)
            except KeyboardInterrupt:
                print("\n(stopped)")
    finally:
        stop_server(proc)


def cmd_ask(args):
    c, proc = ensure_server(args)
    try:
        body = {"messages": [{"role": "user", "content": args.question}], "mode": args.mode, "think": args.think}
        if args.json:
            out = {"text": "", "route": None, "validation": None}
            for ev, d in c.sse("/run", body):
                if ev in ("route", "validation"):
                    out[ev] = d
                elif ev == "done":
                    out.update(text=d["text"], reasoning=d.get("reasoning"), usage=d["usage"], timing=d["timing"])
                elif ev == "error":
                    out["error"] = d["error"]
            print(json.dumps(out, indent=1, ensure_ascii=False))
        else:
            r = show_answer(c.sse("/run", body), args.show_thinking)
            if r.get("error"):
                sys.exit(1)
    finally:
        stop_server(proc)


def cmd_status(args):
    c = Client(args.url, args.api_key)
    if c.health() is None:
        raise SystemExit(f"No Yaob server at {args.url}.")
    print(json.dumps(c.get("/status"), indent=1))


def cmd_conversations(args):
    c = Client(args.url, args.api_key)
    for x in c.get("/api/conversations")["conversations"]:
        print(f"{x['id']}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(x['updated']))}  {x['messages']:>3} msgs  {x['title']}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "serve":
        from .server import main as serve_main
        return serve_main(argv[1:])
    ap = argparse.ArgumentParser(prog="yaob", description="Yaob C4B-N10K command line")
    ap.add_argument("--url", default=os.environ.get("YAOB_URL", "http://127.0.0.1:" + os.environ.get("YAOB_PORT", "8842")))
    ap.add_argument("--api-key", default=os.environ.get("YAOB_API_KEY"))
    ap.add_argument("--device", default=os.environ.get("YAOB_DEVICE", "auto"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the API server")
    ch = sub.add_parser("chat")
    ch.add_argument("--conversation")
    ch.add_argument("--think", dest="think", action="store_true", help="reason before answering (off by default)")
    ch.add_argument("--no-think", dest="think", action="store_false")
    ch.add_argument("--show-thinking", action="store_true")
    ch.add_argument("--mode", default="yaob", choices=["yaob", "core", "retrieval"])
    ak = sub.add_parser("ask")
    ak.add_argument("question")
    ak.add_argument("--think", dest="think", action="store_true", help="reason before answering (off by default)")
    ak.add_argument("--no-think", dest="think", action="store_false")
    ak.add_argument("--show-thinking", action="store_true")
    ak.add_argument("--mode", default="yaob", choices=["yaob", "core", "retrieval"])
    ak.add_argument("--json", action="store_true")
    sub.add_parser("status")
    sub.add_parser("conversations")
    a = ap.parse_args(argv)
    {"chat": cmd_chat, "ask": cmd_ask, "status": cmd_status, "conversations": cmd_conversations}[a.cmd](a)


if __name__ == "__main__":
    main()
