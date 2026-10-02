"""The offline content hub.

This is the piece that answers "how does it download course material without
ever touching the internet". It does exactly what a package manager does -
resolve, verify, install - except the index and the payload are both already
present: `content/library/` is populated by copying a signed pack bundle off a
USB stick, an SD card, or a LAN share.

The student's own device therefore starts empty. On enrolment the tutor:

  1. resolves the courses their programme and year require (`catalog`),
  2. matches those against the packs present in the local library,
  3. verifies each pack's SHA-256 against its own manifest,
  4. copies only the matching packs into `data/installed/<student>/`,
  5. reports, by name, every required course it has no pack for.

Step 5 is the honest part. A student studying UNZA Civil Engineering Year 3
sees exactly which of their eight courses Ethel can teach and which she cannot.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import config
from . import catalog as catalog_mod
from . import sources
from .pack import Pack, PackError, load_pack
from .retrieval import Bm25Index


def _remove_tree(path: Path) -> bool:
    """Delete a directory, coping with Windows read-only bits and stale handles.

    Reinstalling a pack over an existing copy is routine, and on Windows a plain
    rmtree fails often enough that it has to be handled rather than allowed to
    abort an install.
    """
    def onexc(func, target, exc):  # type: ignore[no-untyped-def]
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    for _ in range(3):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=onexc)
            else:  # onerror takes the same (func, path, exc) shape; deprecated in 3.12
                shutil.rmtree(path, onerror=onexc)
        except OSError:
            pass
        if not path.exists():
            return True
        time.sleep(0.3)
    return not path.exists()


def pack_digest(data: dict[str, Any]) -> str:
    """SHA-256 over the pack's content, excluding the integrity block itself.

    Canonical JSON (sorted keys, no incidental whitespace) so the digest depends
    on what the pack *says*, not on how it happens to be formatted. This is what
    lets a pack be signed once at authoring time and verified on every machine it is
    later copied to.
    """
    clone = {k: v for k, v in data.items() if k != "integrity"}
    payload = json.dumps(clone, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class LibraryEntry:
    pack: Pack
    digest: str
    integrity: str  # "ok" | "unsigned" | "mismatch"


def scan_library(library_dir: Path | None = None) -> list[LibraryEntry]:
    """Enumerate every pack physically present on this machine."""
    root = library_dir or config.LIBRARY_DIR
    entries: list[LibraryEntry] = []
    if not root.exists():
        return entries
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            pack = load_pack(child)
        except PackError:
            continue
        digest = pack_digest(pack.data)
        declared = pack.data.get("integrity", {}).get("sha256")
        if not declared:
            integrity = "unsigned"
        elif declared == digest:
            integrity = "ok"
        else:
            integrity = "mismatch"
        entries.append(LibraryEntry(pack=pack, digest=digest, integrity=integrity))
    return entries


def plan_for(programme_id: str, year: int, track: str | None = None) -> dict[str, Any]:
    """What this student needs, and what this machine can actually give them."""
    cat = catalog_mod.load()
    prog = cat.programme(programme_id)
    if not prog:
        raise KeyError(programme_id)

    courses = cat.courses_for(programme_id, year, track)
    available = {}
    for entry in scan_library():
        p = entry.pack
        key = (p.code or p.title).strip().lower()
        available[key] = entry
        available.setdefault(p.title.strip().lower(), entry)

    matched, missing = [], []
    for course in courses:
        key = cat.course_key(course)
        entry = available.get(key)
        row = {
            "code": course.get("code"),
            "title": course["title"],
            "status": course.get("status", "compulsory"),
            "load": course.get("load"),
            "track": course.get("track"),
        }
        if entry and entry.integrity != "mismatch":
            row["pack_id"] = entry.pack.id
            row["lessons"] = len(entry.pack.lessons)
            row["integrity"] = entry.integrity
            matched.append(row)
        else:
            row["reason"] = (
                "pack failed its integrity check"
                if entry else "no course pack for this course is on this machine"
            )
            missing.append(row)

    matched_ids = {row["pack_id"] for row in matched}

    # Packs on this machine that belong to the student's programme but were not
    # matched to a syllabus course. Two cases produce these: a pack for a
    # different year, and - more importantly - any pack at all when the
    # institution publishes no course list. Without this, a student on a
    # programme like the UNZA BAgSc would be offered nothing even though real
    # material for their degree is sitting on the machine.
    extras = [
        {"pack_id": e.pack.id, "code": e.pack.code, "title": e.pack.title,
         "year": e.pack.year,
         "lessons": len(e.pack.lessons),
         "integrity": e.integrity,
         "reason": ("this year's syllabus is not published, so I cannot say which "
                    "of your courses this belongs to"
                    if not courses else
                    f"belongs to year {e.pack.year} of your programme, not year {year}")}
        for e in scan_library()
        if e.pack.programme == programme_id
        and e.pack.id not in matched_ids
        and e.integrity != "mismatch"
    ]

    return {
        "programme": {
            "id": prog["id"],
            "name": prog["name"],
            "institution": prog["institution"],
            "coverage": prog["coverage"],
            "duration_years": prog.get("duration_years"),
            "source_url": prog.get("source_url"),
            "notes": prog.get("notes", []),
            "entry_requirements": prog.get("entry_requirements"),
        },
        "year": year,
        "track": track,
        "syllabus_known": bool(courses),
        "matched": matched,
        "missing": missing,
        "also_available": extras,
    }


def install(student_id: str, pack_ids: list[str]) -> dict[str, Any]:
    """Copy packs from the local library into the student's own shelf."""
    dest_root = config.INSTALLED_DIR / student_id
    dest_root.mkdir(parents=True, exist_ok=True)
    by_id = {e.pack.id: e for e in scan_library()}

    installed, refused = [], []
    for pid in pack_ids:
        entry = by_id.get(pid)
        if entry is None:
            refused.append({"pack_id": pid, "reason": "not present in this machine's library"})
            continue
        if entry.integrity == "mismatch":
            refused.append({"pack_id": pid, "reason": "SHA-256 does not match the manifest"})
            continue
        dest = dest_root / pid
        if dest.exists() and not _remove_tree(dest):
            refused.append({"pack_id": pid,
                            "reason": "the previous copy could not be removed - close "
                                      "anything using it and try again"})
            continue
        shutil.copytree(entry.pack.path, dest)
        (dest / ".installed.json").write_text(
            json.dumps({"pack_id": pid, "sha256": entry.digest,
                        "integrity": entry.integrity}, indent=2), "utf-8"
        )
        installed.append({"pack_id": pid, "integrity": entry.integrity,
                          "lessons": len(entry.pack.lessons)})
    return {"installed": installed, "refused": refused}


def installed_packs(student_id: str) -> list[Pack]:
    root = config.INSTALLED_DIR / student_id
    if not root.exists():
        return []
    packs: list[Pack] = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            try:
                packs.append(load_pack(child))
            except PackError:
                continue
    return packs


class Shelf:
    """A student's installed packs plus a search index over them."""

    def __init__(self, student_id: str, language: str = "en") -> None:
        self.student_id = student_id
        self.language = language or "en"
        self.packs = installed_packs(student_id)
        # Index the student's language and English side by side. Passages carry
        # their language, so retrieval can prefer one and fall back to the other
        # while still telling the student which it answered from.
        self.index = Bm25Index()
        for pack in self.packs:
            self.index.add(pack.passages("en"))
            if self.language != "en":
                self.index.add(pack.passages(self.language))
        # Reference material an administrator uploaded for this programme. It is
        # searchable and quotable but never teachable, and every passage carries
        # kind="reference" so the citation says so.
        self.sources = []
        enr = self._enrolment()
        if enr:
            self.sources = sources.for_programme(enr.get("programme", ""),
                                                 enr.get("year"))
            for rec in self.sources:
                self.index.add(sources.passages(rec["id"]))

    def _enrolment(self) -> dict[str, Any] | None:
        from . import profile as profile_mod
        try:
            return profile_mod.load(self.student_id).get("enrolment")
        except (OSError, ValueError):
            return None

    def pack(self, pack_id: str) -> Pack | None:
        return next((p for p in self.packs if p.id == pack_id), None)

    def lesson(self, pack_id: str, lesson_id: str):
        """The lesson in the student's language, falling back field by field.

        Anything not yet translated keeps its English text and is named in the
        lesson's `_untranslated` list, so the interface marks it instead of
        implying a person checked it.
        """
        pack = self.pack(pack_id)
        if not pack:
            return (None, None)
        return (pack, pack.localised_lesson(lesson_id, self.language))

    def translation_report(self) -> list[dict[str, Any]]:
        """Per-pack completeness in the student's language, for the UI."""
        if self.language == "en":
            return []
        return [{"pack_id": p.id, "code": p.code, "title": p.title,
                 **p.translation_status(self.language)} for p in self.packs]

    def all_lessons(self) -> list[dict[str, Any]]:
        out = []
        for pack in self.packs:
            for lesson in pack.lessons:
                out.append({
                    "pack_id": pack.id,
                    "course_code": pack.code,
                    "course_title": pack.title,
                    "lesson_id": lesson["id"],
                    "title": lesson["title"],
                    "skill": lesson.get("skill"),
                    "prerequisites": lesson.get("prerequisites", []),
                    "minutes": lesson.get("estimated_minutes"),
                })
        return out

    def diagnostic_pool(self) -> list[dict[str, Any]]:
        pool = []
        for pack in self.packs:
            for item in pack.diagnostic_items():
                pool.append(dict(item, pack_id=pack.id))
        return pool
