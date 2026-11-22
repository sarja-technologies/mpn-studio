"""Run Studio's read-only reader for an FG-MPN store (`mpnstudio.mpnnet.fgmpn_net`) on a loopback port.

    python -m mpnstudio.mpnnet.fgmpn_serve ROOT --host 127.0.0.1 --port 8858 [--name NAME]
    python -m mpnstudio.mpnnet.fgmpn_serve stats ROOT

In the packaged app the same entry is `mpn-backend fgmpn-serve ...`; the FG-MPN runtime is inside the bundle.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "stats":
        p = argparse.ArgumentParser(prog="fgmpn-serve stats")
        p.add_argument("root")
        a = p.parse_args(args[1:])
        from fgmpn.store import FGStore
        from .fgmpn_net import load_or_compute_stats, stats_path
        root = Path(a.root)
        st = FGStore(root)
        out = load_or_compute_stats(root, st, write=True)
        if out is None:
            raise SystemExit("this store is too large to count here")
        print(stats_path(root, st.generation))
        return
    p = argparse.ArgumentParser(prog="fgmpn-serve")
    p.add_argument("root")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8858)
    p.add_argument("--name")
    a = p.parse_args(args)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("this server is read-only and for this computer: use a loopback address")
    from .fgmpn_net import serve
    serve(a.root, a.host, a.port, a.name)


if __name__ == "__main__":
    main()
