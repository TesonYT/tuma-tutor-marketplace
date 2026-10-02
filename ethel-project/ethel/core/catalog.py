"""Institution and programme catalogue.

This layer answers "what am I supposed to be studying?" It is data, not
inference. Where an institution does not publish a course list, `coverage` is
`"none"` and the tutor tells the student that plainly rather than producing a
plausible-looking syllabus.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .. import config


@dataclass
class Catalog:
    institutions: list[dict[str, Any]]
    programmes: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    retrieved_on: str

    def institution(self, iid: str) -> dict[str, Any] | None:
        return next((i for i in self.institutions if i["id"] == iid), None)

    def programme(self, pid: str) -> dict[str, Any] | None:
        return next((p for p in self.programmes if p["id"] == pid), None)

    def field(self, fid: str) -> dict[str, Any] | None:
        return next((f for f in self.fields if f["id"] == fid), None)

    def fields_for(self, institution_id: str) -> list[dict[str, Any]]:
        """Fields this institution actually has a catalogued programme for.

        A student can only pick one programme, so the UI walks
        institution -> field -> programme and never offers a combination the
        catalogue cannot back up.
        """
        have = {p["field"] for p in self.programmes if p["institution"] == institution_id}
        return [dict(f, available=f["id"] in have) for f in self.fields]

    def programmes_for(self, institution_id: str, field_id: str) -> list[dict[str, Any]]:
        return [
            p for p in self.programmes
            if p["institution"] == institution_id and p["field"] == field_id
        ]

    def courses_for(
        self, programme_id: str, year: int, track: str | None = None
    ) -> list[dict[str, Any]]:
        prog = self.programme(programme_id)
        if not prog:
            return []
        courses = prog.get("years", {}).get(str(year), [])
        if track:
            courses = [c for c in courses if c.get("track") in (None, track)]
        return courses

    def course_key(self, course: dict[str, Any]) -> str:
        """Stable key used to match a syllabus course against an installed pack."""
        return (course.get("code") or course.get("title", "")).strip().lower()


@lru_cache(maxsize=1)
def load() -> Catalog:
    inst = json.loads((config.CATALOG_DIR / "institutions.json").read_text("utf-8"))
    prog = json.loads((config.CATALOG_DIR / "programmes.json").read_text("utf-8"))
    return Catalog(
        institutions=inst["institutions"],
        programmes=prog["programmes"],
        fields=prog["fields"],
        retrieved_on=prog.get("retrieved_on", ""),
    )
