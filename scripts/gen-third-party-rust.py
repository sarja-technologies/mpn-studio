#!/usr/bin/env python3
"""Write docs/THIRD-PARTY-RUST.md: every Rust crate compiled into the desktop shell, with the licence its
manifest declares. Reads `cargo metadata` for the Linux x86_64 target (normal and build dependencies only).

  (cd src-tauri && cargo metadata --format-version 1 --filter-platform x86_64-unknown-linux-gnu --locked) > meta.json
  python scripts/gen-third-party-rust.py meta.json > docs/THIRD-PARTY-RUST.md
"""
import json, sys, collections

d = json.load(open(sys.argv[1]))
pk = {p["id"]: p for p in d["packages"]}
nodes = {n["id"]: n for n in d["resolve"]["nodes"]}
seen, stack = set(), [d["resolve"]["root"]]
while stack:
    i = stack.pop()
    if i in seen:
        continue
    seen.add(i)
    for dep in nodes[i]["deps"]:
        if any(k["kind"] in (None, "build") for k in dep["dep_kinds"]):
            stack.append(dep["pkg"])
crates = sorted((pk[i] for i in seen if pk[i]["source"]), key=lambda p: (p["name"], p["version"]))
lic = collections.Counter((p.get("license") or "not declared") for p in crates)

print("# Rust crates in the desktop shell\n")
print(f"{len(crates)} crates from crates.io are compiled into the desktop shell (`src-tauri/`), listed with the licence each "
      "crate's own manifest declares. Generated from `Cargo.lock` by `scripts/gen-third-party-rust.py`; regenerate it when "
      "the lock file changes. Where a crate offers a choice (`MIT OR Apache-2.0`), you may use it under either.\n")
print("| Licence expression | Crates |\n|---|---|")
for k, v in lic.most_common():
    print(f"| {k} | {v} |")
print("\n| Crate | Version | Licence | Source |\n|---|---|---|---|")
for p in crates:
    print(f"| {p['name']} | {p['version']} | {p.get('license') or 'not declared'} | {p.get('repository') or 'https://crates.io/crates/' + p['name']} |")
