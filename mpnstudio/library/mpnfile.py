"""The .mpn model file: one portable file holding an MPN model (format "mpn-model/1", specified in docs/MPN-FORMAT.md).

Layout
    0      header, 64 bytes
             magic         8  b"MPNMODEL"
             version       2  little-endian u16, = 1
             flags         2  bit 0: lightweight (the cognitive core is referenced by hash, not included)
             reserved      4  zero
             index_offset  8  u64
             index_length  8  u64
             index_sha256 32  SHA-256 of the index bytes
    64...  members, stored uncompressed, each starting at a multiple of ALIGN (64 KiB) so it can be memory-mapped or
           cloned block-for-block (reflink) straight out of the file
    end    index: UTF-8 JSON {format, manifest, members: [{name, kind, offset, size, sha256}]}

Integrity: the header carries the index hash and the index carries every member's SHA-256. `open()` checks the
header and index (cheap); `verify()` hashes every member; `materialize()` hashes each member as it extracts it and
refuses the file on any mismatch. Nothing is executed from a model file: members are data (GGUF weights, SQLite
network, JSON configuration, text notices) and are never imported as code.

A .mpn file is not a GGUF file. llama.cpp alone cannot run it: the MPN runtime (`mpn run`) reads it, gives the GGUF
members to llama.cpp, and does the retrieval itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import time
from pathlib import Path
from typing import Callable, Iterable

MAGIC = b"MPNMODEL"
VERSION = 1
FORMAT = "mpn-model/1"
ALIGN = 64 << 10
HEADER = struct.Struct("<8sHHIQQ32s")          # 64 bytes
assert HEADER.size == 64
FLAG_LIGHT = 1
MAX_INDEX = 64 << 20
Progress = Callable[[str, int, int], None]


class MpnFileError(Exception):
    pass


def safe_name(name) -> bool:
    """A member name is a relative path of plain parts: no absolute paths, '..', '.', backslashes or NUL."""
    if not isinstance(name, str) or not name or len(name) > 255 or "\\" in name or "\0" in name or name.startswith("/"):
        return False
    return all(p not in ("", ".", "..") for p in name.split("/"))


def _pad(f, align: int = ALIGN) -> None:
    pos = f.tell()
    rem = (-pos) % align
    if rem:
        f.write(b"\0" * rem)


def write(dst: str | Path, manifest: dict, members: Iterable[dict], lightweight: bool = False, progress: Progress | None = None) -> dict:
    """members: [{name, kind, path}] or [{name, kind, data: bytes}]. Writes atomically (to dst.part, then renames).
    Returns the index."""
    dst = Path(dst)
    tmp = dst.with_name(dst.name + ".part")
    members = list(members)
    names = [m["name"] for m in members]
    if len(set(names)) != len(names):
        raise MpnFileError("duplicate member names")
    if not all(safe_name(n) for n in names):
        raise MpnFileError("member names must be relative paths without '..'")
    total = sum((Path(m["path"]).stat().st_size if "path" in m else len(m["data"])) for m in members)
    done = 0
    entries = []
    with tmp.open("wb") as f:
        f.write(b"\0" * HEADER.size)
        for m in members:
            _pad(f)
            off = f.tell()
            h = hashlib.sha256()
            if "path" in m:
                with Path(m["path"]).open("rb") as src:
                    while True:
                        b = src.read(8 << 20)
                        if not b:
                            break
                        h.update(b)
                        f.write(b)
                        done += len(b)
                        if progress:
                            progress(f"writing {m['name']}", done, total)
            else:
                h.update(m["data"])
                f.write(m["data"])
                done += len(m["data"])
            entries.append({"name": m["name"], "kind": m.get("kind", "data"), "offset": off, "size": f.tell() - off, "sha256": h.hexdigest()})
        _pad(f, 4096)
        index = {"format": FORMAT, "written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "manifest": manifest, "members": entries}
        raw = json.dumps(index, ensure_ascii=False, indent=1).encode()
        ioff = f.tell()
        f.write(raw)
        f.seek(0)
        f.write(HEADER.pack(MAGIC, VERSION, FLAG_LIGHT if lightweight else 0, 0, ioff, len(raw), hashlib.sha256(raw).digest()))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dst)
    return index


class MpnFile:
    """An open .mpn file. Reads only the header and the index; members are read on demand."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        with self.path.open("rb") as f:
            hb = f.read(HEADER.size)
            if len(hb) < HEADER.size:
                raise MpnFileError(f"{self.path.name} is too short to be an .mpn model file.")
            magic, ver, flags, _r, ioff, ilen, ihash = HEADER.unpack(hb)
            if magic != MAGIC:
                hint = " It is a GGUF file: import it as a cognitive core instead." if hb[:4] == b"GGUF" else ""
                raise MpnFileError(f"{self.path.name} is not an .mpn model file.{hint}")
            if ver > VERSION:
                raise MpnFileError(f"{self.path.name} uses .mpn format version {ver}; this MPN runtime reads version {VERSION}. Update MPN Studio.")
            if ilen > MAX_INDEX or ioff + ilen > self.size or ioff < HEADER.size:
                raise MpnFileError(f"{self.path.name} is damaged or incomplete (its index lies outside the file).")
            f.seek(ioff)
            raw = f.read(ilen)
        if hashlib.sha256(raw).digest() != ihash:
            raise MpnFileError(f"{self.path.name} is damaged: its index does not match its checksum.")
        try:
            self.index = json.loads(raw)
        except ValueError:
            raise MpnFileError(f"{self.path.name} is damaged: its index is not valid JSON.")
        if self.index.get("format") != FORMAT:
            raise MpnFileError(f"Unknown model format {self.index.get('format')!r}.")
        self.version, self.lightweight = ver, bool(flags & FLAG_LIGHT)
        self.manifest = self.index["manifest"]
        names = [m.get("name") for m in self.index["members"]]
        for n in names:
            if not safe_name(n):
                raise MpnFileError(f"{self.path.name} is not a valid model file: it names a member {n!r} outside the model.")
        if len(set(names)) != len(names):
            raise MpnFileError(f"{self.path.name} is not a valid model file: a member name repeats.")
        self.members = {m["name"]: m for m in self.index["members"]}
        for m in self.members.values():
            if m["offset"] < HEADER.size or m["offset"] + m["size"] > ioff:
                raise MpnFileError(f"{self.path.name} is damaged: member {m['name']} lies outside the data area.")
        self.id = hashlib.sha256(raw).hexdigest()[:16]

    def member(self, name: str) -> dict:
        if name not in self.members:
            raise MpnFileError(f"{self.path.name} has no member {name!r}.")
        return self.members[name]

    def read(self, name: str) -> bytes:
        m = self.member(name)
        if m["size"] > 256 << 20:
            raise MpnFileError(f"{name} is large; extract it instead of reading it into memory.")
        with self.path.open("rb") as f:
            f.seek(m["offset"])
            b = f.read(m["size"])
        if hashlib.sha256(b).hexdigest() != m["sha256"]:
            raise MpnFileError(f"{self.path.name} is damaged: member {name} does not match its checksum.")
        return b

    def json(self, name: str):
        return json.loads(self.read(name))

    def extract(self, name: str, dst: str | Path, progress: Progress | None = None) -> Path:
        """Write one member to dst, verifying its hash while copying. On reflink filesystems the blocks are shared."""
        m = self.member(name)
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        h = hashlib.sha256()
        done = 0
        with self.path.open("rb") as fi, tmp.open("wb") as fo:
            fi.seek(m["offset"])
            left = m["size"]
            while left:
                b = fi.read(min(8 << 20, left))
                if not b:
                    raise MpnFileError(f"{self.path.name} ends inside member {name}.")
                h.update(b)
                fo.write(b)
                left -= len(b)
                done += len(b)
                if progress:
                    progress(f"extracting {name}", done, m["size"])
        if h.hexdigest() != m["sha256"]:
            tmp.unlink(missing_ok=True)
            raise MpnFileError(f"{self.path.name} is damaged: member {name} does not match its checksum. The file was not used.")
        os.replace(tmp, dst)
        return dst

    def verify(self, progress: Progress | None = None) -> dict:
        """Hash every member. Returns {ok, members: [{name, ok}]}; never raises for a bad member."""
        out = []
        with self.path.open("rb") as f:
            for m in self.index["members"]:
                f.seek(m["offset"])
                h, left, done = hashlib.sha256(), m["size"], 0
                while left:
                    b = f.read(min(8 << 20, left))
                    if not b:
                        break
                    h.update(b)
                    left -= len(b)
                    done += len(b)
                    if progress:
                        progress(f"verifying {m['name']}", done, m["size"])
                out.append({"name": m["name"], "ok": left == 0 and h.hexdigest() == m["sha256"]})
        return {"ok": all(x["ok"] for x in out), "members": out}

    def summary(self) -> dict:
        man = self.manifest
        return {"file": str(self.path), "bytes": self.size, "format": FORMAT, "version": self.version, "id": self.id,
                "mode": "lightweight" if self.lightweight else "complete", "name": man.get("name"), "model_id": man.get("model_id"),
                "core": man.get("core"), "encoder": man.get("encoder"), "network": man.get("network"),
                "members": [{"name": m["name"], "kind": m["kind"], "bytes": m["size"]} for m in self.index["members"]]}
