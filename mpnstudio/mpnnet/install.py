# Vendored from the Yaob repository (mpn14b/export_install.py at f8bd650, 2026-11-13): install a downloaded MPN pack for offline use.
"""Install a downloaded MPN pack (compressed shards) so it can be opened offline.

    python -m mpn14b.export_install <downloaded-pack-dir> <install-dir> [--overlay]

Decompresses every `parts/pNN.sqlite.zst` into `<install-dir>/parts/pNN.sqlite`, checks each against `source_sha256` in
the manifest, and copies the manifest and attribution. With `--overlay`, also puts the restricted rows from
`private/restricted-overlay.sqlite.zst` back into their partitions (complete network, private use only). Resumable:
partitions that are already installed and match are skipped. Needs the `zstd` program (or Python 3.14's
compression.zstd) and as much free space as the installed size in the manifest.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


def _decompress(src: Path, dst: Path) -> str:
    h = hashlib.sha256()
    tmp = dst.with_suffix(".part")
    if shutil.which("zstd"):
        p = subprocess.Popen(["zstd", "-dc", "-q", str(src)], stdout=subprocess.PIPE)
        stream = p.stdout
    else:
        from compression import zstd       # Python 3.14+
        stream, p = zstd.open(src, "rb"), None
    with open(tmp, "wb") as f:
        while b := stream.read(8 << 20):
            h.update(b)
            f.write(b)
    if p is not None and p.wait() != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"could not decompress {src}")
    tmp.rename(dst)
    return h.hexdigest()


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(8 << 20):
            h.update(b)
    return h.hexdigest()


def install(src: Path, dst: Path, overlay: bool = False, progress=print) -> dict:
    m = json.loads((src / "manifest.json").read_text())
    need = m["requirements"]["disk_bytes_installed"]
    (dst / "parts").mkdir(parents=True, exist_ok=True)
    have = sum(p.stat().st_size for p in (dst / "parts").glob("p*.sqlite"))
    free = shutil.disk_usage(dst).free
    if free + have < need:
        raise SystemExit(f"not enough space in {dst}: the pack needs {need / 1e9:,.1f} GB installed, {free / 1e9:,.1f} GB free")
    for s in m["shards"]:
        out = dst / "parts" / f"p{s['partition']:02d}.sqlite"
        if out.exists() and out.stat().st_size == s["source_bytes"] and _sha(out) == s["source_sha256"]:
            progress(f"p{s['partition']:02d} already installed")
            continue
        h = _decompress(src / s["path"], out)
        if h != s["source_sha256"]:
            out.unlink()
            raise RuntimeError(f"p{s['partition']:02d}: checksum mismatch after decompression")
        progress(f"p{s['partition']:02d} installed ({s['source_bytes'] / 1e9:.2f} GB)")
    restored = 0
    if overlay:
        ov = dst / "restricted-overlay.sqlite"
        _decompress(src / "private" / "restricted-overlay.sqlite.zst", ov)
        for s in m["shards"]:
            if not s["filtered"]:
                continue
            d = sqlite3.connect(dst / "parts" / f"p{s['partition']:02d}.sqlite")
            d.execute("ATTACH DATABASE ? AS o", (f"file:{ov}?mode=ro",))
            rows = d.execute("SELECT rid, nid, kind, domain, ds, lic, status, conf, lang, title, subj, created, body FROM o.nodes WHERE part=?",
                             (s["partition"],)).fetchall()
            d.execute("BEGIN")
            for r in rows:
                d.execute("INSERT OR IGNORE INTO nodes(rid, nid, kind, domain, ds, lic, status, conf, lang, title, subj, created, body)"
                          " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
                d.execute("INSERT INTO counts VALUES(?,?,?,?,1) ON CONFLICT DO UPDATE SET n=n+1", (r[2], r[3], r[4], r[6]))
            d.execute("COMMIT")
            d.close()
            restored += len(rows)
        ov.unlink()
    for name in ("manifest.json", "ATTRIBUTION.md", "RESTORE.md"):
        if (src / name).exists():
            shutil.copy2(src / name, dst / name)
    return {"installed": str(dst), "partitions": len(m["shards"]), "nodes": m["counts"]["nodes"], "overlay_rows": restored}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    print(json.dumps(install(Path(sys.argv[1]), Path(sys.argv[2]), "--overlay" in sys.argv), indent=1))
