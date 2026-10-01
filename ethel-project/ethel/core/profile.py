"""Student records: enrolment, learner model, mastery, review schedule.

One JSON file per student under `data/students/`. No database, no server, no
account anywhere - a student's whole record can be carried on a memory card
between machines.

The PIN is a local convenience lock (so a shared machine doesn't mix up two
students' progress), not real security. It is hashed with PBKDF2 so the file
doesn't sit around in plaintext, but anyone with the disk can reset it. That is
stated plainly in the README rather than dressed up.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .. import config

_ID_OK = re.compile(r"^[a-z0-9][a-z0-9._-]{1,40}$")


def _now() -> float:
    return time.time()


def student_path(student_id: str) -> Path:
    return config.STUDENTS_DIR / f"{student_id}.json"


def normalise_id(raw: str) -> str:
    sid = re.sub(r"[^a-z0-9._-]+", "-", (raw or "").strip().lower()).strip("-.")
    if not _ID_OK.match(sid):
        raise ValueError(
            "Student ID must be 2-41 characters: letters, digits, dot, dash or underscore."
        )
    return sid


def hash_pin(pin: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 120_000)
    return dk.hex(), salt.hex()


def blank(student_id: str, name: str) -> dict[str, Any]:
    return {
        "schema": "ethel.student/1",
        "id": student_id,
        "name": name,
        "created": _now(),
        # Administrators upload reference material; students do not. The role
        # rides on the record that already exists rather than introducing a
        # second password system to forget about.
        "role": "student",
        "enrolment": None,
        "preferences": {
            "language": "en",
            "voice": "female",
            "pace": "standard",
            "example_first": True,
            "reading_support": False,
        },
        "placement": {"completed": False},
        "mastery": {},
        "lessons": {},
        "reviews": [],
        "log": [],
    }


def is_admin(record: dict[str, Any]) -> bool:
    return record.get("role") == "admin"


def set_role(student_id: str, role: str) -> dict[str, Any]:
    if role not in ("student", "admin"):
        raise ValueError("role must be 'student' or 'admin'")
    rec = load(student_id)
    rec["role"] = role
    save(rec)
    return rec


def exists(student_id: str) -> bool:
    return student_path(student_id).exists()


def load(student_id: str) -> dict[str, Any]:
    return json.loads(student_path(student_id).read_text("utf-8"))


def save(record: dict[str, Any]) -> None:
    """Write a student's record atomically, allowing for Windows.

    Write-to-temp-then-replace is the standard way to avoid a half-written
    record if the machine loses power mid-save - which, for a device running off
    a battery or a solar supply, is a real event rather than a theoretical one.

    On Windows the replace itself can fail with ERROR_ACCESS_DENIED even when
    nothing is wrong: a real-time antivirus scanner opens the newly created temp
    file to inspect it, and holds it for a few milliseconds. Retrying briefly
    clears it. Losing a student's progress because Defender was reading a 2 KB
    file is not an acceptable failure.
    """
    config.ensure_dirs()
    path = student_path(record["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2), "utf-8")

    last: OSError | None = None
    for attempt in range(6):
        try:
            tmp.replace(path)
            return
        except PermissionError as exc:       # transient scanner lock
            last = exc
            time.sleep(0.05 * (attempt + 1))
    # Every retry failed. Write in place rather than lose the record: a torn
    # write is bad, but silently dropping the student's progress is worse, and
    # the caller gets the error either way if this fails too.
    try:
        path.write_text(json.dumps(record, indent=2), "utf-8")
        tmp.unlink(missing_ok=True)
    except OSError:
        raise last if last else OSError(f"could not save {path}")


def create(student_id: str, name: str, pin: str) -> dict[str, Any]:
    if exists(student_id):
        raise ValueError("A student with that ID already exists on this machine.")
    rec = blank(student_id, name)
    rec["pin_hash"], rec["pin_salt"] = hash_pin(pin)
    save(rec)
    return rec


def check_pin(record: dict[str, Any], pin: str) -> bool:
    want = record.get("pin_hash")
    salt = record.get("pin_salt")
    if not want or not salt:
        return True
    got, _ = hash_pin(pin, bytes.fromhex(salt))
    return hmac.compare_digest(got, want)


def list_students() -> list[dict[str, Any]]:
    config.ensure_dirs()
    out = []
    for path in sorted(config.STUDENTS_DIR.glob("*.json")):
        try:
            rec = json.loads(path.read_text("utf-8"))
        except ValueError:
            continue
        out.append({"id": rec["id"], "name": rec.get("name", rec["id"]),
                    "enrolled": bool(rec.get("enrolment"))})
    return out


def note(record: dict[str, Any], kind: str, detail: str) -> None:
    record.setdefault("log", []).append({"t": _now(), "kind": kind, "detail": detail})
    record["log"] = record["log"][-300:]


# --- enrolment -------------------------------------------------------------

def enrol(
    record: dict[str, Any],
    institution: str,
    field: str,
    programme: str,
    year: int,
    track: str | None = None,
) -> dict[str, Any]:
    """Lock the student to exactly one programme.

    A student may only study one programme at a time. Switching is possible but
    explicit: the old enrolment is archived with its progress intact rather than
    quietly merged into the new one.
    """
    if record.get("enrolment"):
        record.setdefault("previous_enrolments", []).append(
            dict(record["enrolment"], archived_on=_now(),
                 mastery=dict(record.get("mastery", {})),
                 lessons=dict(record.get("lessons", {})))
        )
        record["mastery"] = {}
        record["lessons"] = {}
        record["reviews"] = []
        record["placement"] = {"completed": False}
    record["enrolment"] = {
        "institution": institution,
        "field": field,
        "programme": programme,
        "year": int(year),
        "track": track,
        "enrolled_on": _now(),
    }
    note(record, "enrol", f"{programme} year {year}")
    return record["enrolment"]


# --- learner model ---------------------------------------------------------

PRIOR = 0.35  # what we assume before any evidence: "probably not yet mastered"


def mastery_of(record: dict[str, Any], skill: str) -> float:
    return record.get("mastery", {}).get(skill, {}).get("p", PRIOR)


def observe(record: dict[str, Any], skill: str, correct: bool, difficulty: int = 3) -> float:
    """Update the estimate for one skill from one observed answer.

    Exponential moving average, with harder items moving the estimate further.
    Deliberately simple and inspectable - a teacher can read the numbers in the
    student's JSON file and see why the tutor made a decision.
    """
    if not skill:
        return PRIOR
    m = record.setdefault("mastery", {}).setdefault(
        skill, {"p": PRIOR, "attempts": 0, "correct": 0}
    )
    alpha = 0.20 + 0.06 * max(1, min(5, difficulty))
    m["p"] = round(m["p"] + alpha * ((1.0 if correct else 0.0) - m["p"]), 4)
    m["attempts"] += 1
    m["correct"] += 1 if correct else 0
    m["last"] = _now()
    return m["p"]


def has_mastered(record: dict[str, Any], skill: str, cfg: dict[str, Any]) -> bool:
    m = record.get("mastery", {}).get(skill)
    if not m:
        return False
    ped = cfg["pedagogy"]
    return (
        m["p"] >= ped["mastery_threshold"]
        and m["attempts"] >= ped["min_attempts_for_mastery"]
    )


# --- spaced repetition -----------------------------------------------------

def schedule_review(record: dict[str, Any], ref: str, correct: bool) -> None:
    """SM-2 without the ceremony: three grades, doubling intervals."""
    reviews = record.setdefault("reviews", [])
    entry = next((r for r in reviews if r["ref"] == ref), None)
    if entry is None:
        entry = {"ref": ref, "interval_days": 1.0, "ease": 2.3, "due": _now()}
        reviews.append(entry)
    if correct:
        entry["interval_days"] = round(max(1.0, entry["interval_days"] * entry["ease"]), 2)
        entry["ease"] = round(min(2.8, entry["ease"] + 0.05), 2)
    else:
        entry["interval_days"] = 1.0
        entry["ease"] = round(max(1.4, entry["ease"] - 0.25), 2)
    entry["due"] = _now() + entry["interval_days"] * 86400
    record["reviews"] = reviews[-500:]


def due_reviews(record: dict[str, Any]) -> list[dict[str, Any]]:
    now = _now()
    return [r for r in record.get("reviews", []) if r.get("due", 0) <= now]
