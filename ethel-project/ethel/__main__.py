"""Entry point.

The airlock is engaged before anything else is imported, so no module can open a
socket during start-up before the guard is in place.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import airlock

airlock.engage()

from . import config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ethel", description="Ethel - offline AI teacher"
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--doctor", action="store_true",
                        help="print a system report and exit")
    parser.add_argument("--validate", action="store_true",
                        help="validate every pack in the content library and exit")
    parser.add_argument("--allow-network", action="store_true",
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.validate:
        from .core.library import scan_library
        from .core.pack import PackError, load_pack

        ok = True
        root = config.LIBRARY_DIR
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            try:
                pack = load_pack(child)
            except PackError as exc:
                ok = False
                print(f"FAIL  {child.name}: {exc}")
                continue
            from .core.pack import validate
            warnings = validate(pack.data, label=pack.id)
            print(f"OK    {pack.id}  ({len(pack.lessons)} lessons, "
                  f"{len(pack.passages())} passages)")
            for w in warnings:
                print(f"      warning: {w}")
        for entry in scan_library():
            if entry.integrity == "mismatch":
                ok = False
                print(f"FAIL  {entry.pack.id}: SHA-256 does not match its manifest")
        return 0 if ok else 1

    from . import server

    if args.doctor:
        ctx = server.Ctx.__new__(server.Ctx)
        print(json.dumps(server.doctor(ctx), indent=2))
        return 0

    server.serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
