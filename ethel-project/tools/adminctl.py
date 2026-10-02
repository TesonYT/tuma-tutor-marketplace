"""Grant or revoke the administrator role.

    python tools/adminctl.py list
    python tools/adminctl.py grant 2021458392
    python tools/adminctl.py revoke 2021458392

Deliberately a command line tool and not a page in the web interface. Whoever
can run this already has the machine; making it a screen would mean inventing a
way to bootstrap the first administrator, and every scheme for that is either a
default password somebody forgets to change or a first-run window somebody
misses. A person with a keyboard on the machine is the honest gate.

An administrator can upload reference material. That is the whole of the extra
power: they cannot read another student's progress, and there is no route from
here to writing lesson content, because Ethel does not write lesson content.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ethel import airlock  # noqa: E402

airlock.engage()

from ethel.core import profile as profile_mod  # noqa: E402


def cmd_list(_: argparse.Namespace) -> int:
    people = profile_mod.list_students()
    if not people:
        print("No students on this machine yet.")
        print("Somebody has to sign in through the web interface first;")
        print("then grant them the role with:  adminctl.py grant <id>")
        return 0
    for s in people:
        rec = profile_mod.load(s["id"])
        role = rec.get("role", "student")
        mark = "ADMIN" if role == "admin" else "     "
        print(f"  {mark}  {s['id']:14} {s.get('name', '')}")
    if not any(profile_mod.load(s["id"]).get("role") == "admin" for s in people):
        print("\nNobody is an administrator yet, so nobody can upload material.")
    return 0


def _set(student_id: str, role: str) -> int:
    sid = profile_mod.normalise_id(student_id)
    if not profile_mod.exists(sid):
        print(f"No student with id {sid!r} on this machine.")
        print("They must sign in once through the web interface first.")
        return 1
    rec = profile_mod.set_role(sid, role)
    article = "an administrator" if role == "admin" else "a student"
    print(f"{rec.get('name') or sid} is now {article}.")
    if role == "admin":
        print("They will see an Administration tab next time they sign in.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="adminctl")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="who is on this machine, and their role")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("grant", help="make someone an administrator")
    p.add_argument("student_id")
    p.set_defaults(fn=lambda a: _set(a.student_id, "admin"))

    p = sub.add_parser("revoke", help="return someone to being a student")
    p.add_argument("student_id")
    p.set_defaults(fn=lambda a: _set(a.student_id, "student"))

    args = ap.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
