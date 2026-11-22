"""Test fixtures for official releases: a local server that behaves like Hugging Face's `resolve` URLs (with Range requests,
and switches to damage or interrupt a download), and builders for release manifests with real files.

Nothing here names a real organisation; manifests point at the local server (`hosting.type = huggingface` with the server
as HF_ENDPOINT, or `https` with its address as base_url)."""
import hashlib
import json
import os
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ORG = "test-org"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Host:
    def __init__(self):
        self.files: dict[str, bytes] = {}          # "/org/repo/resolve/rev/path" -> bytes
        self.corrupt: set[str] = set()              # URL paths served with one flipped byte
        self.cut: dict[str, int] = {}               # URL path -> bytes sent before the connection is dropped (once)
        self.no_range = False
        self.slow: dict[str, float] = {}            # URL path -> seconds to wait after every 64 KiB sent (to watch or cancel a download)
        self.status: dict[str, int] = {}            # URL path -> forced HTTP status
        self.requests: list[tuple[str, str | None]] = []
        host = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                host.requests.append((path, self.headers.get("Range")))
                if path in host.status:
                    self.send_response(host.status[path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = host.files.get(path)
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path in host.corrupt:
                    data = bytes([data[0] ^ 0xFF]) + data[1:]
                start, code = 0, 200
                rng = self.headers.get("Range")
                if rng and not host.no_range:
                    start = int(rng.split("=")[1].split("-")[0])
                    code = 206
                body = data[start:]
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                if code == 206:
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                self.end_headers()
                if path in host.cut:
                    n = host.cut.pop(path)
                    self.wfile.write(body[:n])
                    self.wfile.flush()
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)       # really drop it: the client must see an incomplete body, not wait
                    except OSError:
                        pass
                    return
                if path in host.slow:
                    import time
                    for i in range(0, len(body), 65536):
                        self.wfile.write(body[i:i + 65536])
                        self.wfile.flush()
                        time.sleep(host.slow[path])
                    return
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()

    def put(self, repo: str, rev: str, path: str, data: bytes):
        self.files[f"/{ORG}/{repo}/resolve/{rev}/{path}"] = data

    def path(self, repo: str, rev: str, path: str) -> str:
        return f"/{ORG}/{repo}/resolve/{rev}/{path}"


def rev_of(seed: str) -> str:
    return hashlib.sha1(seed.encode()).hexdigest()


def fg_store(nodes=(30, 12), seg_bytes=(300_000, 90_000)) -> dict[str, bytes]:
    """The files of a small FG-MPN store: CURRENT, one manifest, segments (opaque bytes; Studio checks them by hash)."""
    segs, files = [], {}
    for i, (n, sz) in enumerate(zip(nodes, seg_bytes), start=1):
        b = hashlib.sha256(f"seg{i}".encode()).digest() * (sz // 32)
        name = f"seg-{i:06d}.fgs"
        files[f"segments/{name}"] = b
        segs.append({"file": name, "kind": "base", "nodes": n, "bytes": len(b), "sha256": sha(b)})
    m = {"format": "fgmpn-manifest/2", "generation": 1, "segments": segs, "created": 1}
    files["manifests/gen-000001.json"] = json.dumps(m).encode()
    files["CURRENT"] = b"gen-000001.json"
    return files


def release(kind: str, rid: str, files: dict[str, bytes], host: Host, repo: str | None = None, version="1.0.0", detail=None, rev=None,
            roles=None, hosting="huggingface", **extra) -> dict:
    repo = repo or rid
    rev = rev or rev_of(rid + version)
    for p, b in files.items():
        host.put(repo, rev, p, b)
    m = {"schema": "fylume-release/1", "kind": kind, "id": rid, "name": rid.replace("-", " ").title(), "version": version, "released_at": "2026-11-22",
         "licence": {"spdx": "CC-BY-SA-4.0", "notes": "test"},
         "hosting": {"primary": {"type": "huggingface", "repo": f"{ORG}/{repo}", "revision": rev}, "mirrors": []},
         "files": [{"path": p, "bytes": len(b), "sha256": sha(b), "role": (roles or {}).get(p, "data")} for p, b in files.items()],
         "totals": {"download_bytes": sum(len(b) for b in files.values()), "file_count": len(files)}, kind: detail or {}, **extra}
    return m


def mpn_release(host, rid="yaob-test-fg", nodes=42, version="1.0.0", **kw) -> dict:
    files = fg_store()
    d = {"format": "fgmpn", "format_version": "0.2.2", "manifest_format": "fgmpn-manifest/2", "generation": 2, "nodes": nodes, "nodes_validated": nodes,
         "relationships": 7, "read_only": True, "studio_min": "0.5.0", "verification": {"parity_failures": 0}}
    d.update(kw.pop("detail", {}))
    return release("mpn", rid, files, host, version=version, detail=d, roles={"CURRENT": "manifest"}, **kw)


def tiny_gguf(arch="llama", name="Tiny", pad=0, pooling=False) -> bytes:
    def s(x: str) -> bytes:
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    kv = [("general.architecture", 8, s(arch)), ("general.name", 8, s(name)), ("general.file_type", 4, struct.pack("<I", 7)),
          (f"{arch}.context_length", 4, struct.pack("<I", 4096)), ("tokenizer.chat_template", 8, s("{{x}}"))]
    if pooling:                                    # an embedding model: the header says how its vectors are pooled
        kv = [k for k in kv if k[0] != "tokenizer.chat_template"] + [(f"{arch}.pooling_type", 4, struct.pack("<I", 3))]
    body = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 2) + struct.pack("<Q", len(kv))
    for k, t, v in kv:
        body += s(k) + struct.pack("<I", t) + v
    for n, shape in (("tok_embd.weight", (64, 32)), ("output.weight", (64, 32))):
        body += s(n) + struct.pack("<I", len(shape)) + b"".join(struct.pack("<Q", d) for d in shape) + struct.pack("<I", 8) + struct.pack("<Q", 0)
    return body + b"\0" * 64 + os.urandom(0) + (hashlib.sha256(name.encode()).digest() * (pad // 32 + 1))[:pad]


def core_release(host, rid="test-core-4b", version="1.0.0", quants=("Q4_K_M",), pad=200_000, arch="llama", pooling=False, **kw) -> dict:
    files = {f"{rid}-{q}.gguf": tiny_gguf(arch=arch, name=f"{rid}-{q}", pad=pad + 1000 * i, pooling=pooling) for i, q in enumerate(quants)}
    files["README.md"] = b"# test core\n"
    d = {"parameters": 4_000_000_000, "architecture": arch, "derivation": "quantisation", "fine_tuned": False, "api": "openai-compatible", "context_length": 4096,
         "base_model": {"id": "someone/base", "licence": "apache-2.0"},
         "formats": [{"kind": "gguf", "quant": q, "file": f"{rid}-{q}.gguf"} for q in quants]}
    return release("core", rid, files, host, version=version, detail=d, roles={"README.md": "docs"}, **kw)


def model_release(host, mpn: dict, core: dict, rid="n10k-c4b-test", version="1.0.0", pairing="N10K-C4B", encoder: dict | None = None, **kw) -> dict:
    def pin(m):
        raw = json.dumps(m).encode()
        host.put(m["hosting"]["primary"]["repo"].split("/")[1], m["hosting"]["primary"]["revision"], "fylume-release.json", raw)
        return {"id": m["id"], "version": m["version"], "repo": m["hosting"]["primary"]["repo"], "revision": m["hosting"]["primary"]["revision"], "manifest_sha256": sha(raw)}
    d = {"pairing": pairing, "mpn": pin(mpn), "core": {**pin(core), "file": core["core"]["formats"][0]["file"]}, "encoder": ({**pin(encoder), "file": encoder["core"]["formats"][0]["file"]} if encoder else None),
         "defaults": {"endpoint_example": "http://127.0.0.1:8080/v1", "model_id": "test-pairing", "settings": {}}, "runtime": {"studio_min": "0.5.0"}}
    m = release("model", rid, {}, host, version=version, detail=d, repo=rid, **kw)
    m["files"] = []
    return m


# ------------------------------------------------------------------------------------------------ the website's catalogue shape
def studio_payload(n=20) -> dict[str, bytes]:
    """`studio/network/yaob-n10k.sqlite` (a real Studio store) and `studio/network/checkpoint/` (a router checkpoint: manifest.json and shards/shared.safetensors)."""
    import tempfile
    import test_builder
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "n.sqlite")
        test_builder.seed(p, n=n).close()
        net = open(p, "rb").read()
    return {"studio/network/yaob-n10k.sqlite": net,
            "studio/network/checkpoint/manifest.json": json.dumps({"id": "ck-test-0001", "encoder": {"name": "Qwen3-Embedding-0.6B", "trained": False}}).encode(),
            "studio/network/checkpoint/shards/shared.safetensors": os.urandom(5000)}


def both_payloads_release(host, rid="n10k-both", version="1.0.0", store_prefix="", **kw) -> dict:
    """An official network carrying both payloads in one repo: FG-MPN (explore) and Studio (answer). Files say which they belong to.
    `store_prefix="store"` keeps the FG-MPN store under store/, as the published Yaob N10K packages do."""
    fg, st = {(store_prefix + "/" if store_prefix else "") + k: v for k, v in fg_store().items()}, studio_payload()
    files = {**fg, **st, "README.md": b"# N10K\n"}
    m = release("mpn", rid, files, host, version=version, roles={(store_prefix + "/" if store_prefix else "") + "CURRENT": "manifest", "README.md": "docs"},
                detail={"format": "fgmpn", "formats": ["fgmpn", "studio-store"], "format_version": "0.2.2", "manifest_format": "fgmpn-manifest/2", "generation": 1,
                        "nodes": 42, "relationships": 7, "read_only": True, "studio_min": "0.5.0"}, **kw)
    for f in m["files"]:
        f["format"] = "fgmpn" if f["path"] in fg else "studio-store" if f["path"] in st else None
        if f["format"] is None:
            del f["format"]
    return m


def as_site(m: dict, host) -> dict:
    """A manifest as the website's catalogue lists it: pinned file urls, licence as text, a block named after the kind."""
    prim = m["hosting"]["primary"]
    repo, rev = prim["repo"].split("/")[1], prim["revision"]
    files = [{"name": f["path"], "url": host.url + host.path(repo, rev, f["path"]), "bytes": f["bytes"], "sha256": f["sha256"], "role": f["role"],
              "format": f.get("format") or "Descriptive label, not a payload name", "platform": "Any", "arch": "", "optional": False, "mirrors": []} for f in m["files"]]
    return {"id": m["id"], "slug": f"{m['id']}-{m['version']}".replace(".", "-"), "name": m["name"], "version": m["version"], "status": "preview",
            "released_at": m["released_at"], "summary": "A fixture.", "lang": "en", "licence": "Node data: CC BY-SA 4.0. Router: source-available, no resale",
            "download": None, "size_bytes": sum(f["bytes"] for f in m["files"]), "links": {"huggingface": None, "release": None, "docs": None, "source": None},
            "install_md": "", "files": files, "previous_versions": [], m["kind"]: m[m["kind"]]}


def site_model(host, mpn_m: dict, core_m: dict, enc_m: dict | None = None, rid="site-pairing", version="1.0.0", by_repo_url=False) -> dict:
    """components[] in the shape of the website's type: {role, id, name, version, repo_url, nodes?, parameters?, bytes?, download?}."""
    def comp(role, m):
        c = {"role": role, "id": m["id"], "name": m["name"], "version": m["version"], "repo_url": f"https://huggingface.co/{m['hosting']['primary']['repo']}",
             "bytes": 1, "download": {"type": "repository", "url": host.url, "filename": None, "bytes": 1, "sha256": None}}
        if by_repo_url:
            del c["id"]
        return c
    comps = [comp("mpn", mpn_m), comp("core", core_m)] + ([comp("encoder", enc_m)] if enc_m else [])
    return {"id": rid, "slug": rid, "name": "Site Pairing", "version": version, "status": "preview", "released_at": "2026-11-22", "summary": "A fixture pairing.", "lang": "en",
            "licence": "Free to use and share", "download": None, "size_bytes": 0, "links": {}, "files": [], "previous_versions": [],
            "model": {"pairing": "N10K-C4B", "mpn": {"name": mpn_m["name"], "nodes": 42, "version": mpn_m["version"]}, "core": {"name": core_m["name"]},
                      "components": comps, "studio_min": "0.5.0"}}


def site_doc(host, models=(), mpns=(), apps=(), cores=()) -> dict:
    return {"schema": "fylume-catalogue/1", "updated_at": "2026-11-22T11:26:15.314Z", "lang": "en", "models": list(models), "mpns": list(mpns), "apps": list(apps), "cores": list(cores)}
