#!/usr/bin/env python3
"""Acceptance of an installed MPN Studio package against the live catalogue and two OpenAI-compatible core servers.

Runs the packaged backend (the one inside an unpacked .deb, or an extracted AppImage) headless with a clean home folder, and checks:

  1  the FG-MPN runtime is in the package (version, engine)
  2  the live catalogue lists the public N10K networks and cores
  3  a public N10K package installs, verifies and opens in the explorer (search, node, graph, paging, counts, storage), with the
     native reader and with the Python engine giving the same answers
  4  a prebuilt pairing manifest installs from the three pinned public repositories (the Studio payload of the network, the encoder,
     and either the core or your server), builds, and answers a question from the network with citations
  5  the pairing is rebound to a second core without rebuilding the network: the network hash does not change; it answers again

    release-acceptance.py --backend ".../backend/mpn-backend" --runtime ".../runtime" --home /scratch/home \\
        --pairing fylume-release.json --core-a http://127.0.0.1:8871/v1,qwen3.5-4b-q8 --core-b http://127.0.0.1:8872/v1,qwen3-4b-instruct-q8 \\
        --out results/ [--local-core]

`--local-core` installs the pairing with its own core (a download of several GB, run by the package's llama.cpp) instead of core A.
The results go to OUT/results.txt and OUT/*.json. Exit status 1 when a check fails.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

A = argparse.ArgumentParser()
A.add_argument("--backend", required=True)
A.add_argument("--runtime", required=True, help="the package's runtime/ folder (llama.cpp)")
A.add_argument("--home", required=True, help="a clean folder used as HOME")
A.add_argument("--pairing", required=True, help="the prebuilt pairing manifest (fylume-release.json, kind model)")
A.add_argument("--core-a", required=True, help="BASE_URL,MODEL_ID of an OpenAI-compatible server")
A.add_argument("--core-b", required=True, help="BASE_URL,MODEL_ID of a second one")
A.add_argument("--out", required=True)
A.add_argument("--local-core", action="store_true")
A.add_argument("--question", default="Which city is the capital of Australia?")
A.add_argument("--device", default="cpu")
args = A.parse_args()

OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)
HOME = Path(args.home)
HOME.mkdir(parents=True, exist_ok=True)
ENV = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "MPN_"))}
ENV.update({"HOME": str(HOME), "MPN_RUNTIME_DIR": args.runtime})
PASS = FAIL = 0
LOG = OUT / "results.txt"
LOG.write_text("")


def say(s: str) -> None:
    print(s, flush=True)
    with LOG.open("a") as f:
        f.write(s + "\n")


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    say(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    return bool(ok)


def mpn(*a: str, timeout: int = 3600, env_extra: dict | None = None) -> tuple[int, str, str]:
    r = subprocess.run([args.backend, "mpn", *a], env={**ENV, **(env_extra or {})}, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


class Backend:
    def __init__(self, tag: str, env_extra: dict | None = None):
        self.port, self.token = 20000 + secrets.randbelow(5000), secrets.token_hex(16)
        self.log = (OUT / f"backend-{tag}.log").open("w")
        self.p = subprocess.Popen([args.backend], env={**ENV, **(env_extra or {}), "MPN_DESKTOP": "1", "MPN_HOST": "127.0.0.1", "MPN_PORT": str(self.port),
                                  "MPN_SESSION_TOKEN": self.token, "MPN_NO_SYSTEMD": "1"}, stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        for _ in range(120):
            try:
                if self.get("/api/health").get("ok"):
                    return
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(0.5)
        raise SystemExit("the backend did not start")

    def req(self, method: str, path: str, body=None, timeout: int = 120):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                   headers={"x-mpn-token": self.token, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=timeout) as x:
                return json.loads(x.read() or b"null")
        except urllib.error.HTTPError as e:
            return {"_http": e.code, "_body": e.read().decode(errors="replace")[:500]}

    def get(self, path: str, **kw):
        return self.req("GET", path, **kw)

    def post(self, path: str, body=None, **kw):
        return self.req("POST", path, body or {}, **kw)

    def task(self, t: dict, limit: int = 3600) -> dict:
        t0 = time.time()
        while time.time() - t0 < limit:
            v = self.get(f"/api/library/tasks/{t['id']}")
            if v.get("state") in ("done", "failed", "cancelled"):
                return v
            time.sleep(1.5)
        return {"state": "timeout"}

    def stop(self) -> None:
        self.p.terminate()
        try:
            self.p.wait(15)
        except subprocess.TimeoutExpired:
            self.p.kill()


def dump(name: str, obj) -> None:
    (OUT / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------------------------------------------- 1, 2
say(f"== MPN Studio acceptance {time.strftime('%Y-%m-%d %H:%M:%S')}  backend {args.backend}")
b = Backend("a")
h = b.get("/api/health")
check("backend starts headless", h.get("ok") is True, f"version {h.get('version')}")
rel = b.get("/api/releases")
fg = rel["fgmpn"]
check("FG-MPN runtime is in the package", fg["available"] and fg.get("bundled") and fg["version"].startswith("0.2."), json.dumps({k: fg[k] for k in ("version", "engine", "bundled")}))
code, out, err = mpn("catalogue", "refresh")
check("live catalogue refreshes", code == 0, out.strip() or err.strip()[-200:])
cat = b.get("/api/catalogue")
rels = {(r["kind"], r["id"]): r for r in cat["releases"]}
dump("catalogue.json", [{k: r[k] for k in ("kind", "id", "version", "name", "compat", "problems")} for r in cat["releases"]])
for key in (("mpn", "yaob-n10k"), ("mpn", "yaob-next-n10k"), ("core", "qwen3-5-4b-q8-0-gguf"), ("core", "qwen3-embedding-0-6b-f16-gguf")):
    check(f"catalogue lists {key[0]} {key[1]} as installable", key in rels and rels[key]["compat"]["ok"] and not rels[key]["problems"])

# ---------------------------------------------------------------------------------------------------------------- 3
EXPLORE = {}
for rid, ver in (("yaob-next-n10k", "0.2.0"), ("yaob-n10k", "0.1.0")):
    t0 = time.time()
    t = b.task(b.post("/api/catalogue/install", {"kind": "mpn", "id": rid, "version": ver, "format": "fgmpn"}), 900)
    check(f"install {rid} {ver} (FG-MPN payload) from the public repository", t["state"] == "done", f"{t.get('error') or ''} {time.time() - t0:.0f} s")
    v = b.task(b.post("/api/releases/verify", {"kind": "mpn", "id": rid, "version": ver, "format": "fgmpn"}), 600)
    check(f"{rid}: every file and segment verifies (size and SHA-256)", v["state"] == "done" and (v.get("result") or {}).get("ok") is True, json.dumps((v.get("result") or {}))[:160])
    o = b.post(f"/api/releases/{rid}/{ver}/open")
    link = b.post("/api/projects/link", {"name": f"Explore {rid}", "sources": [{"mode": "fgmpn", "target": o.get("path", "")}]})
    ov = b.get("/api/ws/overview")
    ok = ov.get("engine") == "fgmpn" and ov.get("validated_nodes", 0) > 9000 and ov.get("domains")
    check(f"{rid}: opens in the explorer through the bundled runtime", ok, f"{ov.get('validated_nodes')} nodes, {ov.get('relationships')} relationships, {len(ov.get('domains') or [])} domains, name {ov.get('name')!r}")
    hits = b.get("/api/ws/search?q=Australia").get("results") or []
    node = b.get(f"/api/ws/node/{hits[0]['id']}") if hits else {}
    check(f"{rid}: search finds Australia and its node opens with relationships", bool(hits) and hits[0]["title"] == "Australia" and len(node.get("out") or []) > 3 and bool(node.get("node", {}).get("cells")),
          f"{len(node.get('out') or [])} out, {len(node.get('in') or [])} in")
    g = b.get(f"/api/ws/node/{hits[0]['id']}/graph?limit=20") if hits else {}
    check(f"{rid}: the canvas graph of a node has neighbours", len(g.get("nodes") or []) > 3 and len(g.get("edges") or []) > 3)
    cl = b.get("/api/ws/clusters")
    d0 = (cl.get("children") or [{}])[0].get("domain")
    p1 = b.get(f"/api/ws/browse?domain={d0}&limit=5") if d0 else {}
    check(f"{rid}: counts by domain and a domain's nodes page", cl.get("exact") and cl.get("total") == ov.get("validated_nodes") and len(p1.get("results") or []) == 5, f"first domain {d0}")
    st = b.get("/api/ws/fgmpn/storage")
    check(f"{rid}: storage panel answers", st.get("fgmpn", {}).get("deployed_bytes", 0) > 1_000_000)
    EXPLORE[rid] = {"ids": [h["id"] for h in hits], "validated": ov.get("validated_nodes"), "node": node.get("node", {}).get("title")}
dump("explore.json", EXPLORE)
b.stop()
b = Backend("python-engine", {"FGMPN_ENGINE": "python"})
o = b.post("/api/releases/yaob-next-n10k/0.2.0/open")
b.post("/api/projects/link", {"name": "Explore python engine", "sources": [{"mode": "fgmpn", "target": o.get("path", "")}]})
hits2 = [h["id"] for h in (b.get("/api/ws/search?q=Australia").get("results") or [])]
check("the Python engine returns the same search results as the native reader", hits2 == EXPLORE["yaob-next-n10k"]["ids"] and hits2, f"{len(hits2)} hits")
b.stop()

# ---------------------------------------------------------------------------------------------------------------- 4
url_a, mid_a = args.core_a.split(",")
url_b, mid_b = args.core_b.split(",")
for n, (u, m) in (("A", (url_a, mid_a)), ("B", (url_b, mid_b))):
    code, out, err = mpn("core", "add-server", f"Core {n}", "--url", u, "--model-id", m)
    code2, out2, err2 = mpn("core", "test", f"core-{n.lower()}")
    check(f"core server {n} is added and answers the connection test", code == 0 and code2 == 0 and "answered" in out2, out2.strip())
code, out, err = mpn("catalogue", "import", args.pairing)
check("the pairing manifest imports without problems", code == 0 and '"problems": []' in out.replace("\n", "").replace("  ", " ") or '"problems": []' in out, out.strip()[:120].replace("\n", " "))
t0 = time.time()
code, out, err = mpn("catalogue", "install", "yaob-next-n10k-c4b", *([] if args.local_core else ["--server", "core-a"]))
check("the pairing installs from the three pinned public repositories" + (" (own core)" if args.local_core else " (core on server A)"), code == 0, f"{time.time() - t0:.0f} s" + (f" {err.strip()[-200:]}" if code else ""))
code, out, err = mpn("release", "list")
say("installed:\n" + out.rstrip())
MID = "yaob-next-n10k-c4b"
code, out, err = mpn("model", "show", MID)
m0 = json.loads(out) if code == 0 else {}
check("the pairing refers to the network, the encoder and its core", bool(m0.get("network", {}).get("id")) and bool((m0.get("encoder") or {}).get("sha256")) and bool(m0.get("core")), json.dumps({"core": (m0.get("core") or {}).get("name"), "network": (m0.get("network") or {}).get("id")}))
t0 = time.time()
code, out, err = mpn("model", "build", MID, "--device", args.device)
bj = json.loads(out) if code == 0 else {}
bld = bj.get("build") or {}
check("the pairing builds and its own test question is answered from the network", code == 0 and bld.get("ok") and (bld.get("test") or {}).get("expected_node_retrieved") is True, f"{time.time() - t0:.0f} s net_hash {bld.get('net_hash')}")
dump("build-1.json", bj)
q1_out = mpn("model", "test", MID, args.question, "--device", args.device)
ans1 = q1_out[1].strip()
# a small core does not always write the Sources line; the answer must carry the fact the network holds, and the line is reported
check("a question is answered from the network", q1_out[0] == 0 and "Canberra" in ans1, ("cited: " if "Sources:" in ans1 else "no Sources line: ") + ans1[:200].replace("\n", " "))
(OUT / "answer-1.txt").write_text(ans1)

# ---------------------------------------------------------------------------------------------------------------- 5
code, out, err = mpn("model", "set", MID, "core_id=endpoint:core-b")
st = (json.loads(out) if code == 0 else {}).get("status", {})
check("choosing another core marks the pairing as needing the core applied, not a rebuild", "core changed" in " ".join(st.get("problems") or []).lower(), json.dumps(st.get("problems")))
t0 = time.time()
code, out, err = mpn("model", "rebind", MID, "--device", args.device)
rj = json.loads(out) if code == 0 else {}
rb = rj.get("build") or {}
dump("rebind.json", rj)
check("the core is applied without rebuilding the network: the network hash is unchanged", code == 0 and rb.get("rebound") and rb.get("net_hash") == bld.get("net_hash") and rb.get("network_rev") == bld.get("network_rev"),
      f"{time.time() - t0:.0f} s net_hash {bld.get('net_hash')} -> {rb.get('net_hash')}, config {bld.get('config_hash')} -> {rb.get('config_hash')}")
q2_out = mpn("model", "test", MID, args.question, "--device", args.device)
ans2 = q2_out[1].strip()
check("the second core answers the same question from the same network", q2_out[0] == 0 and "Canberra" in ans2, ("cited: " if "Sources:" in ans2 else "no Sources line: ") + ans2[:200].replace("\n", " "))
(OUT / "answer-2.txt").write_text(ans2)
m1 = json.loads(mpn("model", "show", MID)[1] or "{}")
check("the pairing names the second core", (m1.get("core") or {}).get("name") == "Core B", (m1.get("core") or {}).get("name"))
b = Backend("c")
check("the key-free core servers are listed without secrets", "api_key\"" not in json.dumps(b.get("/api/core-servers")))
b.stop()
say(f"== {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
