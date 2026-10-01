"""The course pack format.

A pack is one course (ideally one syllabus course, e.g. CBU "EE 220"). It is a
directory containing a single `pack.json`. Everything the tutor may ever say
about that course lives inside it - lessons, worked examples, practice items,
the misconceptions it knows how to repair, and the provenance of the material.

Shape:

    {
      "schema": "ethel.pack/1",
      "id": "cbu-ee220-dc-circuits",
      "course": {"code": "EE 220", "title": "...", "institution": "cbu",
                 "programme": "cbu-beng-electrical", "year": 2},
      "source": {"authored_by": "...", "licence": "...", "reviewed_by": null},
      "languages": ["en"],
      "lessons": [ {
          "id": "l1", "title": "...", "skill": "ohms-law",
          "prerequisites": [],
          "hook": "...",
          "segments": [ {"id": "s1", "heading": "...", "body": "...",
                         "check": {...}} ],
          "worked_example": {"prompt": "...", "steps": ["..."]},
          "practice": [ {...} ],
          "apply": {"prompt": "...", "guidance": "..."},
          "recap": ["..."],
          "misconceptions": [ {"id": "m1", "signal": "...", "repair": "..."} ]
      } ],
      "diagnostic": [ {"id": "d1", "skill": "...", "difficulty": 3, ...} ]
    }

An item (check / practice / diagnostic) is:

    {"id": "...", "skill": "...", "difficulty": 1-5, "type": "mcq"|"numeric"|"short",
     "prompt": "...", "options": ["..."], "answer": 0 | "42" | ["keyword", ...],
     "tolerance": 0.02, "hint": "...", "explanation": "...",
     "misconception": {"1": "m1"}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import translation
from .retrieval import Passage

SCHEMA = "ethel.pack/1"


class PackError(ValueError):
    pass


@dataclass
class Pack:
    path: Path
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def course(self) -> dict[str, Any]:
        return self.data.get("course", {})

    @property
    def code(self) -> str:
        return self.course.get("code") or ""

    @property
    def title(self) -> str:
        return self.course.get("title") or self.id

    @property
    def programme(self) -> str | None:
        return self.course.get("programme")

    @property
    def year(self) -> int | None:
        return self.course.get("year")

    @property
    def lessons(self) -> list[dict[str, Any]]:
        return self.data.get("lessons", [])

    def lesson(self, lesson_id: str) -> dict[str, Any] | None:
        return next((l for l in self.lessons if l["id"] == lesson_id), None)

    def diagnostic_items(self) -> list[dict[str, Any]]:
        return self.data.get("diagnostic", [])

    def skills(self) -> list[str]:
        seen: list[str] = []
        for lesson in self.lessons:
            s = lesson.get("skill")
            if s and s not in seen:
                seen.append(s)
        return seen

    def misconception(self, lesson_id: str, mid: str) -> dict[str, Any] | None:
        lesson = self.lesson(lesson_id) or {}
        return next(
            (m for m in lesson.get("misconceptions", []) if m["id"] == mid), None
        )

    # ---------------------------------------------------------- languages

    @property
    def languages(self) -> list[str]:
        """Languages this pack carries text for, English first."""
        return translation.available(self.data)

    def localised_lesson(self, lesson_id: str, language: str = "en") -> dict[str, Any] | None:
        """A lesson with translated text swapped in where a person wrote it.

        Untranslated fields keep their English text and are listed in
        `_untranslated`, so the interface can mark them instead of implying the
        whole lesson is in the student's language.
        """
        lesson = self.lesson(lesson_id)
        if lesson is None:
            return None
        return translation.localise(self.data, lesson, language)

    def translation_status(self, language: str) -> dict[str, Any]:
        return translation.completeness(self.data, language)

    def audio_path(self, lesson_id: str, language: str, key: str) -> Path | None:
        """A human recording for one piece of a lesson, if the pack ships one.

        A recording always beats synthesis, and for Lozi it is the only option
        that exists at all.
        """
        tr = (self.data.get("translations") or {}).get(language) or {}
        lt = (tr.get("lessons") or {}).get(lesson_id) or {}
        audio = lt.get("audio") or {}
        rel: str | None
        if key.startswith("segment:"):
            rel = (audio.get("segments") or {}).get(key.split(":", 1)[1])
        else:
            rel = audio.get(key)
        if not rel:
            return None
        candidate = (self.path / rel).resolve()
        # Never let a pack reach outside its own directory.
        try:
            candidate.relative_to(self.path.resolve())
        except ValueError:
            return None
        return candidate if candidate.exists() else None

    def passages(self, language: str = "en") -> list[Passage]:
        """Flatten the pack into retrievable passages.

        Only teaching prose is indexed. Practice answers and explanations are
        deliberately excluded so a student cannot pull an answer key out of the
        tutor by asking the question back at it.

        Passing a language indexes that language's text where a person has
        written it. Untranslated pieces are skipped rather than indexed in
        English, so a Bemba search never silently returns English prose - the
        caller decides whether to fall back, and tells the student when it does.
        """
        out: list[Passage] = []
        for source in self.lessons:
            lesson = (source if language in ("en", "")
                      else translation.localise(self.data, source, language))
            lid, ltitle = lesson["id"], lesson["title"]
            untranslated = set(lesson.get("_untranslated") or [])
            if language not in ("en", "") and "whole lesson" in untranslated:
                continue

            def add(section: str, text: str, skip: bool = False,
                    lid: str = lid, ltitle: str = ltitle) -> None:
                if skip or not text or not text.strip():
                    return
                out.append(
                    Passage(
                        id=f"{self.id}:{lid}:{section}:{len(out)}",
                        pack_id=self.id,
                        course_code=self.code,
                        course_title=self.title,
                        lesson_id=lid,
                        lesson_title=ltitle,
                        section=section,
                        text=text.strip(),
                        language=language or "en",
                    )
                )

            add("Why this matters", lesson.get("hook", ""), "hook" in untranslated)
            for seg in lesson.get("segments", []):
                add(seg.get("heading") or "Explanation", seg.get("body", ""),
                    f"segment {seg.get('id')} body" in untranslated)
            we = lesson.get("worked_example")
            if we:
                body = we.get("prompt", "") + "\n" + "\n".join(we.get("steps", []))
                add("Worked example", body, "worked_example" in untranslated)
            for m in lesson.get("misconceptions", []):
                mid = m.get("id")
                stale = (f"misconception {mid} signal" in untranslated
                         or f"misconception {mid} repair" in untranslated)
                add("Common mistake", f"{m.get('signal','')}\n{m.get('repair','')}", stale)
            recap = lesson.get("recap") or []
            add("Recap", "\n".join(recap), "recap" in untranslated)
        return out


def load_pack(directory: Path) -> Pack:
    manifest = directory / "pack.json"
    if not manifest.exists():
        raise PackError(f"{directory.name}: no pack.json")
    try:
        data = json.loads(manifest.read_text("utf-8"))
    except ValueError as exc:
        raise PackError(f"{directory.name}: pack.json is not valid JSON ({exc})") from exc
    validate(data, label=directory.name)
    return Pack(path=directory, data=data)


def validate(data: dict[str, Any], label: str = "pack") -> list[str]:
    """Raise on anything structurally broken; return non-fatal warnings."""
    if data.get("schema") != SCHEMA:
        raise PackError(f"{label}: schema must be {SCHEMA!r}, got {data.get('schema')!r}")
    for key in ("id", "course", "lessons"):
        if key not in data:
            raise PackError(f"{label}: missing required key {key!r}")

    warnings: list[str] = []
    seen_lessons: set[str] = set()
    for lesson in data["lessons"]:
        for key in ("id", "title", "segments"):
            if key not in lesson:
                raise PackError(f"{label}: lesson missing {key!r}")
        if lesson["id"] in seen_lessons:
            raise PackError(f"{label}: duplicate lesson id {lesson['id']!r}")
        seen_lessons.add(lesson["id"])
        if not lesson.get("skill"):
            warnings.append(f"lesson {lesson['id']} has no skill tag; mastery cannot be tracked")
        if not lesson.get("practice"):
            warnings.append(f"lesson {lesson['id']} has no practice items; it will read as a lecture")
        for seg in lesson["segments"]:
            if "body" not in seg:
                raise PackError(f"{label}: segment in {lesson['id']} has no body")
            if not seg.get("check"):
                warnings.append(
                    f"segment {seg.get('id', '?')} in {lesson['id']} has no check-for-understanding"
                )
        for item in list(lesson.get("practice", [])) + list(lesson.get("segments", [])):
            check = item.get("check", item) if isinstance(item, dict) else {}
            if check and check.get("type") == "mcq":
                opts = check.get("options") or []
                ans = check.get("answer")
                if not isinstance(ans, int) or not (0 <= ans < len(opts)):
                    raise PackError(
                        f"{label}: mcq {check.get('id','?')} in {lesson['id']} has an out-of-range answer"
                    )
    if not data.get("source", {}).get("reviewed_by"):
        warnings.append(
            "pack has no reviewed_by; the tutor will label it as not yet reviewed by a subject teacher"
        )
    return warnings
