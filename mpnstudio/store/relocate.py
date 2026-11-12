"""Relocatable references to checkpoints.

A store records the absolute path where a checkpoint was written (for example
/home/user/mpn-studio/data/banks/main/checkpoints/ck-261111-200127-6f01ff on a remote machine).
That path means nothing on another machine, and the weights are not copied along with the store.
`resolve_checkpoint` keeps the stored path as the first choice and otherwise looks for the checkpoint
by id under the directories listed in MPN_CHECKPOINT_ROOTS (os.pathsep separated), so the same
project keeps working when its checkpoints are moved, mounted elsewhere or imported later.
The store itself is never rewritten: the recorded path stays as the provenance of where it was trained.
"""
from __future__ import annotations

import os
from pathlib import Path


def roots() -> list[Path]:
    return [Path(p) for p in os.environ.get("MPN_CHECKPOINT_ROOTS", "").split(os.pathsep) if p]


def resolve_checkpoint(stored_path: str | os.PathLike | None, ck_id: str | None = None) -> Path | None:
    """Return an existing directory for the checkpoint, or None if it is not available on this machine."""
    if stored_path:
        p = Path(stored_path)
        if p.exists():
            return p
        ck_id = ck_id or p.name
    if not ck_id:
        return None
    for root in roots():
        for cand in (root / ck_id, root / "checkpoints" / ck_id, *root.glob(f"*/checkpoints/{ck_id}"), *root.glob(f"banks/*/checkpoints/{ck_id}")):
            if (cand / "manifest.json").is_file():
                return cand
    return None
