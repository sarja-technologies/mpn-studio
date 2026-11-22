"""Cognitive cores (and retrieval encoders) known to MPN Studio.

A core is a GGUF file stored once, by content: <data>/cores/<id>/ where id is the first 16 hex digits of the file's
SHA-256. Any number of models refer to it by that hash, so importing the same file twice, or using one core in many
models, never duplicates gigabytes. A vision projector can be attached to a core.

    core.json       what was read from the GGUF header, the hash, where it came from
    model.gguf      the weights (copied; on btrfs/XFS the kernel shares the blocks instead of copying)
    projector.gguf  optional multimodal projector

Mode "reference" leaves the file where it is and records its path and hash instead (no copy; the file must stay).

Attaching a node network to a core never changes the core's weights. MPN Studio does not fine-tune.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from . import gguf

ARCHS = json.loads((Path(__file__).parent / "data" / "llama_archs.json").read_text())
Progress = Callable[[str, int, int], None]


class CoreError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def library_dir() -> Path:
    from ..desktop import paths
    d = paths.data_dir() / "cores"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha256_file(p: Path, progress: Progress | None = None, label: str = "hashing") -> str:
    h = hashlib.sha256()
    total, done = p.stat().st_size, 0
    with p.open("rb") as f:
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            h.update(b)
            done += len(b)
            if progress:
                progress(label, done, total)
    return h.hexdigest()


def copy_file(src: Path, dst: Path, progress: Progress | None = None) -> str:
    """Copy with copy_file_range: the kernel shares blocks on reflink filesystems and copies in-kernel elsewhere.
    Returns 'reflink-or-kernel-copy' or 'copy'."""
    total, done = src.stat().st_size, 0
    tmp = dst.with_name(dst.name + ".part")
    with src.open("rb") as fi, tmp.open("wb") as fo:
        try:
            while done < total:
                n = os.copy_file_range(fi.fileno(), fo.fileno(), min(1 << 30, total - done))
                if n == 0:
                    break
                done += n
                if progress:
                    progress("copying", done, total)
            how = "kernel copy (shared blocks where the filesystem supports it)"
        except OSError:
            fi.seek(0)
            fo.seek(0)
            fo.truncate()
            done = 0
            while True:
                b = fi.read(8 << 20)
                if not b:
                    break
                fo.write(b)
                done += len(b)
                if progress:
                    progress("copying", done, total)
            how = "copy"
        fo.flush()
        os.fsync(fo.fileno())
    if tmp.stat().st_size != total:
        tmp.unlink()
        raise CoreError("the copy is incomplete (disk full?)")
    os.replace(tmp, dst)
    return how


def compatibility(info: dict) -> dict:
    """What the bundled llama.cpp can do with a GGUF file, from its header alone."""
    arch, kind = info.get("architecture"), info.get("kind")
    notes, ok = [], True
    if arch not in ARCHS["gguf_architectures"]:
        ok = False
        notes.append(f"Architecture '{arch}' is not known to the bundled llama.cpp ({ARCHS['llama_cpp']}). It cannot be run.")
    if kind == "core" and not info.get("chat_template"):
        notes.append("The file has no chat template. It can complete text but will not follow chat turns reliably.")
    if kind == "encoder" and info.get("pooling") is None:
        notes.append("No pooling type: llama.cpp will pool by the last token.")
    if kind == "projector":
        notes.append("A vision projector. Attach it to the core it was made for.")
    return {"runnable": ok, "llama_cpp": ARCHS["llama_cpp"], "notes": notes}


def _meta_path(cid: str) -> Path:
    return library_dir() / cid / "core.json"


def get(cid: str) -> dict:
    p = _meta_path(cid)
    if not p.exists():
        raise CoreError(f"No core {cid} in the library.", 404)
    m = json.loads(p.read_text())
    m["path"] = str(weights_path(m))
    m["present"] = Path(m["path"]).exists()
    if m.get("projector"):
        m["projector"]["path"] = str(p.parent / "projector.gguf") if (p.parent / "projector.gguf").exists() else m["projector"].get("reference")
    return m


def weights_path(m: dict) -> Path:
    return Path(m["reference"]) if m.get("reference") else library_dir() / m["id"] / "model.gguf"


def find_by_hash(sha: str) -> dict | None:
    cid = sha[:16]
    try:
        m = get(cid)
    except CoreError:
        return None
    return m if m.get("sha256") == sha else None


def list_cores() -> list[dict]:
    out = []
    for p in sorted(library_dir().glob("*/core.json")):
        try:
            m = get(p.parent.name)
        except (CoreError, ValueError):
            continue
        out.append(m)
    return sorted(out, key=lambda m: m.get("imported", ""), reverse=True)


def _write_meta(cid: str, m: dict) -> None:
    p = _meta_path(cid)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, indent=1, ensure_ascii=False))
    os.replace(tmp, p)


def import_gguf(path: str | Path, mode: str = "copy", name: str | None = None, progress: Progress | None = None,
                attach_to: str | None = None, known_sha256: str | None = None, release: dict | None = None) -> dict:
    """Add a GGUF file to the library. Returns the core's record. Importing a file that is already there returns the
    existing record (matched by content hash)."""
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise CoreError(f"No file at {src}.")
    if mode not in ("copy", "reference", "move"):
        raise CoreError("mode must be copy, reference or move")
    try:
        info = gguf.describe(src)
    except gguf.GGUFError as e:
        raise CoreError(str(e))
    comp = compatibility(info)
    if info["kind"] == "projector":
        if not attach_to:
            raise CoreError("This is a vision projector, not a cognitive core. Import the core first, then attach the projector to it.")
        core = get(attach_to)
        d = library_dir() / attach_to
        sha = known_sha256 or sha256_file(src, progress)
        if mode == "copy":
            copy_file(src, d / "projector.gguf", progress)
            core["projector"] = {"sha256": sha, "bytes": info["bytes"], "parameters": info["parameters"], "quantization": info["quantization"],
                                 "file": src.name}
        else:
            core["projector"] = {"sha256": sha, "bytes": info["bytes"], "parameters": info["parameters"], "quantization": info["quantization"],
                                 "file": src.name, "reference": str(src)}
        core.pop("path", None)
        core.pop("present", None)
        _write_meta(attach_to, core)
        return get(attach_to)
    sha = known_sha256 or sha256_file(src, progress)
    existing = find_by_hash(sha)
    if existing and existing["present"]:
        existing["already_present"] = True
        return existing
    cid = sha[:16]
    d = library_dir() / cid
    d.mkdir(parents=True, exist_ok=True)
    how = None
    if mode == "copy":
        how = copy_file(src, d / "model.gguf", progress)
    elif mode == "move":        # a file Studio itself downloaded: take it into the library without a second copy
        try:
            os.replace(src, d / "model.gguf")
        except OSError:
            shutil.move(str(src), d / "model.gguf")
        how = "moved"
    meta = {"format": "mpn-studio-core/1", "id": cid, "sha256": sha, "kind": info["kind"], "name": name or info["name"],
            "info": info, "compatibility": comp, "imported": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": {"file": str(src), "how": how or "reference"}, **({"reference": str(src)} if mode == "reference" else {}),
            **({"release": release} if release else {})}
    _write_meta(cid, meta)
    return get(cid)


def users_of(cid: str) -> list[dict]:
    from . import models
    out = []
    for m in models.list_models():
        if (m.get("core") or {}).get("id") == cid or (m.get("encoder") or {}).get("id") == cid:
            out.append({"id": m["id"], "name": m["name"]})
    return out


def remove(cid: str) -> dict:
    """Remove a core that no model uses. The folder is moved to the trash, not destroyed."""
    m = get(cid)
    users = users_of(cid)
    if users:
        raise CoreError(f"{m['name']} is used by {', '.join(u['name'] for u in users)}. Change those models first.", 409)
    from ..desktop import paths
    t = paths.trash_dir() / f"core-{cid}-{int(time.time())}"
    t.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(library_dir() / cid), t)
    return {"removed": cid, "moved_to": str(t)}


# ------------------------------------------------------------------------------------------ Hugging Face folders
def inspect_hf(folder: str | Path) -> dict:
    d = Path(folder).expanduser().resolve()
    cfg = d / "config.json"
    if not cfg.exists():
        raise CoreError(f"{d} has no config.json; it is not a Hugging Face model folder.")
    c = json.loads(cfg.read_text())
    archs = c.get("architectures") or []
    weights = sorted(p.name for p in d.glob("*.safetensors")) or sorted(p.name for p in d.glob("pytorch_model*.bin"))
    supported = [a for a in archs if a in ARCHS["hf_architectures"]]
    notes = []
    if not archs:
        notes.append("config.json names no architecture.")
    elif not supported:
        notes.append(f"{', '.join(archs)} cannot be converted by llama.cpp {ARCHS['llama_cpp']}'s converter, so it cannot run in MPN Studio.")
    if not weights:
        notes.append("No weight files (*.safetensors or pytorch_model*.bin) in the folder.")
    return {"folder": str(d), "architectures": archs, "convertible": bool(supported and weights), "model_type": c.get("model_type"),
            "weights": weights, "notes": notes,
            "bytes": sum(p.stat().st_size for p in d.glob("*") if p.is_file())}


def converter() -> dict:
    """The Python used to convert Hugging Face folders: settings compute.python, checked for the converter's packages."""
    from ..desktop import settings
    py = (settings.load().get("compute") or {}).get("python")
    script = convert_script()
    if not script:
        return {"ready": False, "reason": "The llama.cpp converter is not installed with this copy of MPN Studio."}
    if not py:
        return {"ready": False, "reason": "Converting needs a Python environment with torch, transformers, safetensors and sentencepiece. "
                                          "Choose one in Settings, Compute, or convert with llama.cpp's convert_hf_to_gguf.py and import the GGUF."}
    probe = "import importlib.util as u,json;print(json.dumps({m:bool(u.find_spec(m)) for m in ['torch','transformers','safetensors','sentencepiece','numpy']}))"
    try:
        r = subprocess.run([py, "-c", probe], capture_output=True, text=True, timeout=60)
        have = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"ready": False, "reason": f"{py} could not be run: {e}"}
    missing = [k for k, v in have.items() if not v]
    if missing:
        return {"ready": False, "reason": f"{py} lacks {', '.join(missing)}.", "python": py}
    return {"ready": True, "python": py, "script": str(script)}


def convert_script() -> Path | None:
    from ..engine.runtime import runtime_dir
    p = runtime_dir() / "convert" / "convert_hf_to_gguf.py"
    return p if p.exists() else None


def import_hf(folder: str | Path, outtype: str = "q8_0", name: str | None = None, log: Callable[[str], None] | None = None) -> dict:
    """Convert a Hugging Face folder to GGUF with llama.cpp's converter, then import the result."""
    info = inspect_hf(folder)
    if not info["convertible"]:
        raise CoreError(" ".join(info["notes"]) or "This folder cannot be converted.")
    cv = converter()
    if not cv["ready"]:
        raise CoreError(cv["reason"], 409)
    if outtype not in ("f16", "bf16", "q8_0", "f32", "auto"):
        raise CoreError("outtype must be one of f16, bf16, q8_0, f32, auto")
    work = Path(tempfile.mkdtemp(prefix="convert-", dir=library_dir()))
    out = work / "model.gguf"
    script = Path(cv["script"])
    env = dict(os.environ, PYTHONPATH=str(script.parent / "gguf-py") + os.pathsep + str(script.parent), HF_HUB_OFFLINE="1")
    cmd = [cv["python"], str(script), info["folder"], "--outtype", outtype, "--outfile", str(out)]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=work)
        tail = []
        for line in p.stdout:
            tail = (tail + [line.rstrip()])[-30:]
            if log:
                log(line.rstrip())
        if p.wait() != 0 or not out.exists():
            raise CoreError("The conversion failed:\n" + "\n".join(tail[-12:]))
        rec = import_gguf(out, mode="copy", name=name)
        rec_meta = json.loads(_meta_path(rec["id"]).read_text())
        rec_meta["source"] = {"hf_folder": info["folder"], "architectures": info["architectures"], "converted_with": f"llama.cpp {ARCHS['llama_cpp']}",
                              "outtype": outtype}
        _write_meta(rec["id"], rec_meta)
        return get(rec["id"])
    finally:
        shutil.rmtree(work, ignore_errors=True)
