"""Local `mpn` commands: build and run MPN models on this computer, without the MPN Studio window.

  mpn run MODEL.mpn [--chat | --ask "question"] [--port 8850] [--host 127.0.0.1] [--api-key K] [--device auto|cuda|vulkan|cpu]
                    [--context N] [--core FILE.gguf] [--encoder FILE.gguf]
        no --chat/--ask: serve the model's OpenAI-compatible API until Ctrl-C
  mpn info MODEL.mpn            what the file contains
  mpn verify MODEL.mpn          check every member against its SHA-256

  mpn core list | import FILE.gguf [--reference] [--name N] [--projector-for ID] | import-hf FOLDER [--outtype q8_0] | show ID | remove ID
  mpn network list | create NAME | import-yaob FOLDER | import-store FILE NAME | duplicate ID NAME
              | specialise ID NAME [--mpn ID ...] [--q TEXT] [--type T] | merge TARGET SOURCE [--both] | compare A B | stats ID
              | delete-nodes ID [--mpn ID ...] [--q TEXT] [--all] [--yes]
  mpn model list | create NAME --core ID --network ID [--encoder ID] | show ID | set ID key=value ... | build ID [--device D]
            | export ID DEST [--lightweight] | test ID "question"

Data lives in the same folders as the desktop app (~/.local/share/mpn-studio).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

LOCAL = {"run", "info", "verify", "core", "network", "model", "serve-root"}


def _progress():
    last = [0.0, ""]

    def p(label, done, total):
        now = time.time()
        if now - last[0] > 0.5 or done == total:
            last[0] = now
            pct = 100 * done / total if total else 100
            sys.stderr.write(f"\r{label}: {pct:5.1f} %  ({done / 1e9:.2f} of {total / 1e9:.2f} GB)   ")
            if done == total:
                sys.stderr.write("\n")
            sys.stderr.flush()
    return p


def _out(x):
    print(json.dumps(x, indent=1, ensure_ascii=False, default=str))


def _filter(a) -> dict:
    f = {}
    if getattr(a, "mpn", None):
        f["mpn"] = [int(x) for x in a.mpn]
    if getattr(a, "q", None):
        f["q"] = a.q
    if getattr(a, "type", None):
        f["type"] = a.type
    return f


# ------------------------------------------------------------------------------------------- run / info / verify
def cmd_info(a):
    from .library import mpnfile
    _out(mpnfile.MpnFile(a.file).summary())


def cmd_verify(a):
    from .library import mpnfile
    f = mpnfile.MpnFile(a.file)
    r = f.verify(_progress())
    for m in r["members"]:
        print(("ok      " if m["ok"] else "DAMAGED ") + m["name"])
    print("All members match their checksums." if r["ok"] else "The file is damaged.")
    sys.exit(0 if r["ok"] else 1)


def cmd_serve_root(a):
    from .engine.server import serve
    serve(Path(a.root), Path(a.data or Path(a.root) / "data"), a.host, a.port, a.api_key or os.environ.get("MPN_API_KEY"), a.device, a.context)


def cmd_run(a):
    from .library import mpnfile, runner
    from .engine.server import is_loopback
    try:
        f = mpnfile.MpnFile(a.file)
    except (mpnfile.MpnFileError, OSError) as e:
        raise SystemExit(f"error: {e}")
    serving = not a.chat and not a.ask
    if serving:     # refuse before extracting gigabytes, not after
        if not is_loopback(a.host) and not (a.api_key or os.environ.get("MPN_API_KEY")):
            raise SystemExit("error: listening beyond this computer needs --api-key (or MPN_API_KEY).")
        if not _port_free(a.host, a.port):
            raise SystemExit(f"error: port {a.port} on {a.host} is already in use. Choose another with --port.")
    s = f.summary()
    print(f"{s['name']}: {s['mode']} model, core {s['core']['name']} ({s['core']['quantization']}), "
          f"{(s['network'] or {}).get('nodes', '?')} nodes", file=sys.stderr)
    try:
        root = runner.prepare_mpn(a.file, _progress(), core_file=a.core, encoder_file=a.encoder)
    except (runner.RunError, mpnfile.MpnFileError) as e:
        raise SystemExit(f"error: {e}")
    ctx = a.context or runner.context_of(root)
    if serving:
        from .engine.server import serve
        return serve(root, root / "data", a.host, a.port, a.api_key or os.environ.get("MPN_API_KEY"), a.device, ctx)
    run = runner.Run("cli", root, ctx, a.device)
    try:
        print("Loading the model ...", file=sys.stderr)
        runner.wait_ready(run)
        from .engine import cli as ecli
        ns = argparse.Namespace(url=run.url, api_key=run.api_key, conversation=None, think=False, mode="yaob", show_thinking=False,
                                question=a.ask, json=a.json)
        os.environ["YAOB_ROOT"] = str(root)
        if a.ask:
            ecli.cmd_ask(ns)
        else:
            ecli.cmd_chat(ns)
    except runner.RunError as e:
        raise SystemExit(f"error: {e}")
    finally:
        run.stop()


# ------------------------------------------------------------------------------------------- cores
def cmd_core(a):
    from .library import cores
    if a.action == "list":
        for c in cores.list_cores():
            i = c["info"]
            print(f"{c['id']}  {c['kind']:<9} {c['name'][:36]:<36} {i['architecture']:<10} {i['parameters'] / 1e9:6.2f} B  {i['quantization'] or '':<7} "
                  f"{i['bytes'] / 2**30:5.2f} GiB  {'ok' if c['compatibility']['runnable'] else 'NOT RUNNABLE'}")
    elif a.action == "import":
        _out(cores.import_gguf(a.arg, mode="reference" if a.reference else "copy", name=a.name, progress=_progress(), attach_to=a.projector_for))
    elif a.action == "import-hf":
        _out(cores.import_hf(a.arg, outtype=a.outtype, name=a.name, log=lambda s: print(s, file=sys.stderr)))
    elif a.action == "show":
        _out(cores.get(a.arg))
    elif a.action == "remove":
        _out(cores.remove(a.arg))


# ------------------------------------------------------------------------------------------- networks
def cmd_network(a):
    from .library import networks
    from .desktop import projects
    from .store import netops
    from .store.db import Store
    ar = a.args
    if a.action == "list":
        for n in networks.list_networks():
            print(f"{n['id']:<28} {n.get('nodes', '?'):>8} nodes  {n.get('mpns', '?'):>3} MPNs  {'router' if n.get('router') else '      '}  {n['name']}")
    elif a.action == "create":
        _out(networks.create(ar[0]))
    elif a.action == "import-yaob":
        _out(networks.import_yaob_release(ar[0], ar[1] if len(ar) > 1 else "Yaob N10K"))
    elif a.action == "import-store":
        _out(networks.import_store(ar[0], ar[1]))
    elif a.action == "duplicate":
        _out(networks.duplicate(ar[0], ar[1]))
    elif a.action == "specialise":
        _out(networks.specialise(ar[0], ar[1], _filter(a)))
    elif a.action == "merge":
        _out(networks.merge(ar[0], ar[1], _filter(a) or None, on_conflict="both" if a.both else "skip"))
    elif a.action == "compare":
        _out(networks.compare(ar[0], ar[1]))
    elif a.action == "stats":
        _out(networks.stats(ar[0]))
    elif a.action == "delete-nodes":
        f = _filter(a)
        if not f and not a.all:
            raise SystemExit("Give a filter (--mpn, --q, --type) or --all.")
        st = Store(projects.store_path(ar[0]))
        try:
            ids = netops.select_ids(st, f)
            if not a.yes:
                ans = input(f"Delete {len(ids)} nodes from {ar[0]}? This is one revision and can be undone. [y/N] ")
                if ans.strip().lower() != "y":
                    return
            _out(netops.bulk_delete(st, ids, progress=lambda d, t: sys.stderr.write(f"\r{d}/{t}")))
        finally:
            st.close()


def _port_free(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)     # as the server binds
        try:
            sk.bind((host, port))
            return True
        except OSError:
            return False


# ------------------------------------------------------------------------------------------- models
def cmd_model(a):
    from .library import models, runner
    ar = a.args
    if a.action == "list":
        for m in models.list_models():
            print(f"{m['id']:<28} {m['status']['state']:<12} core {((m.get('core') or {}).get('name') or '-')[:28]:<28} network {(m.get('network') or {}).get('id') or '-'}")
    elif a.action == "create":
        _out(models.create(ar[0], core_id=a.core, network_id=a.network, encoder_id=a.encoder))
    elif a.action == "show":
        _out(models.get(ar[0]))
    elif a.action == "set":
        patch = {}
        for kv in ar[1:]:
            k, _, v = kv.partition("=")
            try:
                patch[k] = json.loads(v)
            except ValueError:
                patch[k] = v
        _out(models.update(ar[0], patch))
    elif a.action == "build":
        m = models.build(ar[0], log=lambda s: print(s, file=sys.stderr), device=a.device)
        _out({"status": m["status"], "build": m["build"]})
    elif a.action == "export":
        _out(runner.export(ar[0], ar[1], "lightweight" if a.lightweight else "complete", _progress()))
    elif a.action == "test":
        root = models.build_root(ar[0])
        run = runner.Run("cli-test", root, (models.get(ar[0]).get("context") or {}).get("tokens"), a.device)
        try:
            runner.wait_ready(run)
            from .engine import cli as ecli
            ecli.cmd_ask(argparse.Namespace(url=run.url, api_key=run.api_key, question=" ".join(ar[1:]), mode="yaob", think=False, json=a.json,
                                            show_thinking=False))
        finally:
            run.stop()


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="mpn", description="MPN Studio: build and run MPN models on this computer.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a .mpn model")
    r.add_argument("file")
    r.add_argument("--chat", action="store_true", help="chat in the terminal")
    r.add_argument("--ask", help="ask one question and exit")
    r.add_argument("--json", action="store_true")
    r.add_argument("--host", default="127.0.0.1")
    r.add_argument("--port", type=int, default=8850)
    r.add_argument("--api-key")
    r.add_argument("--device", default="auto", choices=["auto", "cuda", "vulkan", "cpu"])
    r.add_argument("--context", type=int)
    r.add_argument("--core", help="the core GGUF for a lightweight model (checked against its hash)")
    r.add_argument("--encoder", help="the encoder GGUF for a lightweight model")
    r.set_defaults(fn=cmd_run)
    i = sub.add_parser("info")
    i.add_argument("file")
    i.set_defaults(fn=cmd_info)
    v = sub.add_parser("verify")
    v.add_argument("file")
    v.set_defaults(fn=cmd_verify)
    s = sub.add_parser("serve-root", help=argparse.SUPPRESS)
    s.add_argument("--root", required=True)
    s.add_argument("--data")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8850)
    s.add_argument("--api-key")
    s.add_argument("--device", default="auto")
    s.add_argument("--context", type=int)
    s.set_defaults(fn=cmd_serve_root)
    c = sub.add_parser("core")
    c.add_argument("action", choices=["list", "import", "import-hf", "show", "remove"])
    c.add_argument("arg", nargs="?")
    c.add_argument("--reference", action="store_true", help="do not copy; keep the file where it is")
    c.add_argument("--name")
    c.add_argument("--projector-for")
    c.add_argument("--outtype", default="q8_0")
    c.set_defaults(fn=cmd_core)
    n = sub.add_parser("network")
    n.add_argument("action", choices=["list", "create", "import-yaob", "import-store", "duplicate", "specialise", "merge", "compare", "stats", "delete-nodes"])
    n.add_argument("args", nargs="*")
    n.add_argument("--mpn", action="append")
    n.add_argument("--q")
    n.add_argument("--type")
    n.add_argument("--both", action="store_true")
    n.add_argument("--all", action="store_true")
    n.add_argument("--yes", action="store_true")
    n.set_defaults(fn=cmd_network)
    m = sub.add_parser("model")
    m.add_argument("action", choices=["list", "create", "show", "set", "build", "export", "test"])
    m.add_argument("args", nargs="*")
    m.add_argument("--core")
    m.add_argument("--network")
    m.add_argument("--encoder")
    m.add_argument("--device", default="auto")
    m.add_argument("--lightweight", action="store_true")
    m.add_argument("--json", action="store_true")
    m.set_defaults(fn=cmd_model)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:      # library errors carry a message meant for the user
        if type(e).__name__ in ("CoreError", "ModelError", "RunError", "MpnFileError", "ProjectError", "GGUFError"):
            raise SystemExit(f"error: {e}")
        raise
