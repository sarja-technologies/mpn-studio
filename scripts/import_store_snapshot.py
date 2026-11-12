#!/usr/bin/env python3
"""Adopt an existing MPN Studio SQLite store (for example a snapshot taken from the DGX Spark) as a local project.

    python scripts/import_store_snapshot.py <store.sqlite> "Project name" [--detail "where it came from"]

The source file is opened read-only. The copy is made with SQLite's online backup (safe for WAL stores) and
integrity-checked. Projects land in ~/.local/share/mpn-studio/projects/ (XDG_DATA_HOME applies).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mpnstudio.desktop import paths, projects  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("store")
ap.add_argument("name")
ap.add_argument("--detail", default="")
a = ap.parse_args()
paths.ensure()
slug = projects.import_store_file(a.store, a.name, a.detail, kind="spark-snapshot" if a.detail else "store-file")
print(slug, projects.summarize(projects.store_path(slug)))
