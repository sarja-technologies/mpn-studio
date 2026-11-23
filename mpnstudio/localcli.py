"""Local `mpn` commands: build and run MPN models on this computer, without the MPN Studio window.

  mpn run MODEL.mpn [--chat | --ask "question"] [--port 8850] [--host 127.0.0.1] [--api-key K] [--device auto|cuda|vulkan|cpu]
                    [--context N] [--core FILE.gguf] [--encoder FILE.gguf]
        no --chat/--ask: serve the model's OpenAI-compatible API until Ctrl-C
  mpn info MODEL.mpn            what the file contains
  mpn verify MODEL.mpn          check every member against its SHA-256

  mpn core list | import FILE.gguf [--reference] [--name N] [--projector-for ID] | import-hf FOLDER [--outtype q8_0] | show ID | remove ID
  mpn core add-server NAME --url http://HOST:PORT/v1 --model-id ID [--api-key-env VAR | --api-key-stdin] [--context N] [--max-output N]
                           a core on any OpenAI-compatible server (llama.cpp, vLLM, Ollama, hosted); its key is never printed
  mpn core test SERVER_ID | servers | remove-server SERVER_ID
  mpn network list | create NAME | import-yaob FOLDER | import-mpn FILE.mpn [NAME] | import-fgmpn FOLDER [--copy] [--verify size|full] | import-store FILE NAME | duplicate ID NAME
              | specialise ID NAME [--mpn ID ...] [--q TEXT] [--type T] | merge TARGET SOURCE [--both] | compare A B | stats ID
              | delete-nodes ID [--mpn ID ...] [--q TEXT] [--all] [--yes]
  mpn model list | create NAME --core ID|endpoint:SERVER_ID --network ID [--encoder ID] | show ID | set ID key=value ... | build ID [--device D]
            | rebind ID          apply a changed core without rebuilding the network snapshot
            | export ID DEST [--lightweight] | test ID "question"

  mpn catalogue list [--kind mpn|core|model] | refresh | url | import FILE | install ID [--version V] [--format QUANT] [--server SERVER_ID]
  mpn release list | verify ID VERSION | remove KIND ID VERSION       what is installed from the catalogue
  mpn package inspect FOLDER | import FOLDER_OR_ZIP [--verify size|full] [--no-pairing]
                           a Yaob package (manifest.json fylume-package/1): checksums verified, network and GGUF files registered where they
                           are, pairing created. A zip is extracted once into Studio's folder.

Data lives in the same folders as the desktop app (~/.local/share/mpn-studio).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

LOCAL = {"run", "info", "verify", "core", "network", "model", "serve-root", "catalogue", "release", "package"}


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
    elif a.action in ("add-server", "test", "servers", "remove-server"):
        from .library import coreconf
        if a.action == "servers":
            for c in coreconf.list_servers():
                t = c.get("last_test") or {}
                print(f"{c['id']:<24} {c['base_url']:<44} {c['model_id']:<28} key: {c['key_source'] or 'none':<22} {('ok' if t.get('ok') else t.get('code')) if t else 'not tested'}")
        elif a.action == "add-server":
            key = sys.stdin.readline().strip() if a.api_key_stdin else None
            body = {"name": a.arg, "base_url": a.url, "model_id": a.model_id, "context_tokens": a.context, "max_output_tokens": a.max_output,
                    "api_key_env": a.api_key_env}
            if key:
                body["api_key"] = key
            _out(coreconf.save(body))
        elif a.action == "test":
            r = coreconf.test_saved(a.arg)
            print(("ok: " if r["ok"] else f"{r['code']}: ") + r["message"] + (f"  ({r['latency_ms']} ms)" if r.get("latency_ms") is not None else ""))
            for w in r.get("warnings") or []:
                print("note:", w)
            sys.exit(0 if r["ok"] else 1)
        else:
            _out(coreconf.remove(a.arg))


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
    elif a.action == "import-mpn":
        _out(networks.import_mpn_network(ar[0], ar[1] if len(ar) > 1 else None, _progress()))
    elif a.action == "import-fgmpn":
        from .catalogue import install
        _out(install.install_local_fgmpn(ar[0], ar[1] if len(ar) > 1 else None, a.copy, lambda l, d, t: sys.stderr.write(f"\r{l[:50]:<50} {100 * d / max(t, 1):5.1f} %"), None, a.verify))
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
    elif a.action == "rebind":
        m = models.rebind(ar[0], log=lambda s: print(s, file=sys.stderr), device=a.device)
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


def _task_progress():
    last = [0.0]

    def p(label, done, total):
        if time.time() - last[0] > 0.4 or done >= total:
            last[0] = time.time()
            sys.stderr.write(f"\r{label[:48]:<48} {100 * done / max(total, 1):5.1f} %  ({done / 1e6:,.1f} of {total / 1e6:,.1f} MB)   ")
            sys.stderr.flush()
    return p


def cmd_catalogue(a):
    from .catalogue import install, manifest, source
    ar = a.args
    if a.action == "url":
        print(source.url())
    elif a.action == "refresh":
        r = source.refresh()
        print(f"{r['releases']} releases from {r['url']}" + (f"; skipped {len(r['skipped'])}" if r["skipped"] else ""))
    elif a.action == "import":
        _out(source.import_file(ar[0]))
    elif a.action == "list":
        c = source.load()
        print(f"catalogue {c['source']['url']}" + ("" if c["source"]["fetched_at"] else "  (not fetched yet: `mpn catalogue refresh`)"))
        for r in c["releases"]:
            if a.kind and r["kind"] != a.kind:
                continue
            inst = install.get_installed(r["kind"], r["id"], r["version"])
            flag = "installed" if inst else ("INVALID" if r["problems"] else ("incompatible" if r["compat"]["reasons"] else ""))
            print(f"{r['kind']:<6} {r['id']:<30} {r['version']:<9} {r['totals']['download_bytes'] / 1e9:8.2f} GB  {(r['licence']['spdx'] or '?'):<14} {flag}")
    elif a.action == "install":
        r = source.find(a.kind, ar[0], a.version)
        if not r:
            raise SystemExit(f"error: {ar[0]} is not in the catalogue. `mpn catalogue refresh`, or `mpn catalogue import FILE`.")
        pr = _task_progress()
        try:
            rec = install.install_model(r, pr, None, a.server, a.format) if r["kind"] == "model" else install.install(r, pr, None, a.format)
        except (install.InstallError, source.CatalogueError) as e:
            raise SystemExit(f"error: {e}")
        except Exception as e:
            if type(e).__name__ == "DownloadError":
                raise SystemExit(f"error: {e}")
            raise
        sys.stderr.write("\n")
        _out(rec)


def cmd_release(a):
    from .catalogue import install
    ar = a.args
    if a.action == "list":
        for r in install.installed():
            print(f"{r['kind']:<5} {r['id']:<30} {r['version']:<16} {r.get('format') or '':<12} {r.get('nodes') or '':>10}  {'verified ' + r['verified_at'] if r.get('verified_at') else ''}")
    elif a.action == "verify":
        v = install.verify_installed(ar[0] if len(ar) == 3 else "mpn", ar[-2], ar[-1], _task_progress())
        sys.stderr.write("\n")
        print("All files match." if v["ok"] else "PROBLEMS:\n  " + "\n  ".join(v["problems"]))
        sys.exit(0 if v["ok"] else 1)
    elif a.action == "remove":
        _out(install.uninstall(ar[0], ar[1], ar[2]))


def cmd_package(a):
    from .catalogue import package
    if a.action == "inspect":
        _out(package.inspect(a.args[0]))
    else:
        r = package.import_package(a.args[0], lambda l, d, t: sys.stderr.write(f"\r{l[:60]:<60} {100 * d / max(t, 1):5.1f} %"), None, a.verify, not a.no_pairing)
        sys.stderr.write("\n")
        _out(r)


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
    c.add_argument("action", choices=["list", "import", "import-hf", "show", "remove", "add-server", "test", "servers", "remove-server"])
    c.add_argument("--url")
    c.add_argument("--model-id")
    c.add_argument("--api-key-env")
    c.add_argument("--api-key-stdin", action="store_true", help="read the key from standard input (never from the command line)")
    c.add_argument("--context", type=int)
    c.add_argument("--max-output", type=int)
    c.add_argument("arg", nargs="?")
    c.add_argument("--reference", action="store_true", help="do not copy; keep the file where it is")
    c.add_argument("--name")
    c.add_argument("--projector-for")
    c.add_argument("--outtype", default="q8_0")
    c.set_defaults(fn=cmd_core)
    n = sub.add_parser("network")
    n.add_argument("action", choices=["list", "create", "import-yaob", "import-store", "import-mpn", "import-fgmpn", "duplicate", "specialise", "merge", "compare", "stats", "delete-nodes"])
    n.add_argument("--copy", action="store_true", help="import-fgmpn: copy the store into Studio's folder instead of using it where it is")
    n.add_argument("--verify", choices=["size", "full"], default="full", help="import-fgmpn: size = manifest and file sizes only (a store of tens of GB); full = every SHA-256")
    n.add_argument("args", nargs="*")
    n.add_argument("--mpn", action="append")
    n.add_argument("--q")
    n.add_argument("--type")
    n.add_argument("--both", action="store_true")
    n.add_argument("--all", action="store_true")
    n.add_argument("--yes", action="store_true")
    n.set_defaults(fn=cmd_network)
    m = sub.add_parser("model")
    m.add_argument("action", choices=["list", "create", "show", "set", "build", "rebind", "export", "test"])
    m.add_argument("args", nargs="*")
    m.add_argument("--core")
    m.add_argument("--network")
    m.add_argument("--encoder")
    m.add_argument("--device", default="auto")
    m.add_argument("--lightweight", action="store_true")
    m.add_argument("--json", action="store_true")
    m.set_defaults(fn=cmd_model)
    ct = sub.add_parser("catalogue", help="official releases")
    ct.add_argument("action", choices=["list", "refresh", "url", "import", "install"])
    ct.add_argument("args", nargs="*")
    ct.add_argument("--kind", choices=["mpn", "core", "model"])
    ct.add_argument("--version")
    ct.add_argument("--format", help="for a core: the quantisation or file to download")
    ct.add_argument("--server", help="for a pairing: use this configured core server instead of downloading the core")
    ct.set_defaults(fn=cmd_catalogue)
    pk = sub.add_parser("package", help="Yaob packages (core + network + manifest)")
    pk.add_argument("action", choices=["inspect", "import"])
    pk.add_argument("args", nargs="+")
    pk.add_argument("--verify", choices=["size", "full"], default="full", help="full = every SHA-256 (default); size = files and sizes only")
    pk.add_argument("--no-pairing", action="store_true", help="register the network and the GGUF files, create no pairing")
    pk.set_defaults(fn=cmd_package)
    rl = sub.add_parser("release", help="installed releases")
    rl.add_argument("action", choices=["list", "verify", "remove"])
    rl.add_argument("args", nargs="*")
    rl.set_defaults(fn=cmd_release)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:      # library errors carry a message meant for the user
        if type(e).__name__ in ("CoreError", "ModelError", "RunError", "MpnFileError", "ProjectError", "GGUFError", "CoreServerError", "InstallError", "CatalogueError", "DownloadError", "FgError", "PackageError"):
            raise SystemExit(f"error: {e}")
        raise
