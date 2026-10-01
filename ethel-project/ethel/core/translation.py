"""Lesson text in a language other than the one it was authored in.

The rule this module exists to enforce: **Ethel never invents a translation.**
She shows what a person wrote and reviewed, and where nothing has been written
she shows the English and says so plainly. A machine-translated definition of
"invitation to treat" or "Kirchhoff's current law" that nobody has checked is
exactly the confident-sounding nonsense the rest of the design refuses.

That makes translations a content problem rather than a runtime one, so they
live in the pack, next to the material they translate, and carry the same kind
of provenance the pack itself does: who wrote it, who reviewed it, when.

Shape, inside a pack's `pack.json`:

    "languages": ["en", "bem"],
    "translations": {
      "bem": {
        "provenance": {
          "translator":   "name",          // required
          "translated_on":"2026-08-27",
          "reviewed_by":  null,            // null until a second person checks
          "reviewed_on":  null,
          "method": "human" | "post-edited-machine",
          "notes": "dialect, terminology decisions, anything the next person needs"
        },
        "course": {"title": "..."},
        "lessons": {
          "l1": {
            "title": "...", "hook": "...",
            "segments": {"s1": {"heading": "...", "body": "...", "recap": "..."}},
            "worked_example": {"prompt": "...", "steps": ["...", "..."]},
            "recap": ["...", "..."],
            "items":  {"c1": {"prompt": "...", "options": ["...", "..."]}},
            "audio":  {"hook": "audio/bem/l1-hook.wav",
                       "segments": {"s1": "audio/bem/l1-s1.wav"}}
          }
        }
      }
    }

Three deliberate constraints:

* **Multiple-choice answers are never translated.** The answer is an index, so
  it is language-independent by construction. A translated `options` list must
  have exactly the same length and order as the English one; the validator
  rejects it otherwise. This means translating an item can never change which
  answer is correct - a whole class of translation bug simply cannot occur.
* **Short-answer marking stays in the authored language unless terms are
  supplied.** Grading looks for required terms; those are language-specific, so
  an untranslated short item is presented in English rather than marked wrongly.
* **Recorded audio beats synthesis.** If a pack ships a recording for a segment,
  it is used. For Lozi that is currently the only option: no text-to-speech
  model for Lozi exists.
"""

from __future__ import annotations

import copy
from typing import Any

# Fields that carry teaching prose, in the order a lesson presents them. Used by
# both the completeness report and the translator export so the two can never
# drift apart.
PROSE_FIELDS = ("title", "hook", "worked_example", "recap")


def available(pack_data: dict[str, Any]) -> list[str]:
    """Languages this pack actually carries text for, English first."""
    langs = ["en"]
    for code in (pack_data.get("translations") or {}):
        if code not in langs:
            langs.append(code)
    return langs


def provenance(pack_data: dict[str, Any], language: str) -> dict[str, Any] | None:
    block = (pack_data.get("translations") or {}).get(language)
    return (block or {}).get("provenance") if block else None


def is_reviewed(pack_data: dict[str, Any], language: str) -> bool:
    p = provenance(pack_data, language) or {}
    return bool(p.get("reviewed_by"))


def _tr_lesson(pack_data: dict[str, Any], language: str,
               lesson_id: str) -> dict[str, Any]:
    block = (pack_data.get("translations") or {}).get(language) or {}
    return (block.get("lessons") or {}).get(lesson_id) or {}


def localise(pack_data: dict[str, Any], lesson: dict[str, Any],
             language: str) -> dict[str, Any]:
    """Return the lesson with translated text swapped in where it exists.

    Anything without a translation keeps its English text and is named in
    `_untranslated`, so the interface can mark it rather than pretending the
    whole lesson is in the student's language.
    """
    if language == "en" or not language:
        return lesson
    tr = _tr_lesson(pack_data, language, lesson.get("id", ""))
    if not tr:
        out = copy.deepcopy(lesson)
        out["_language"] = "en"
        out["_untranslated"] = ["whole lesson"]
        return out

    out = copy.deepcopy(lesson)
    missing: list[str] = []

    for field in ("title", "hook"):
        if tr.get(field):
            out[field] = tr[field]
        elif lesson.get(field):
            missing.append(field)

    seg_tr = tr.get("segments") or {}
    for seg in out.get("segments", []):
        st = seg_tr.get(seg.get("id", "")) or {}
        for field in ("heading", "body", "recap"):
            if st.get(field):
                seg[field] = st[field]
            elif seg.get(field):
                missing.append(f"segment {seg.get('id')} {field}")
        if st.get("audio"):
            seg["_audio"] = st["audio"]

    # A worked example is only translated if *all* of it is. A half-translated
    # one - English prompt, translated steps - is worse than an English one,
    # because it reads as though someone checked it.
    we = out.get("worked_example")
    if we:
        we_tr = tr.get("worked_example") or {}
        want_steps = len(we.get("steps") or [])
        got_steps = len(we_tr.get("steps") or [])
        whole = bool(we_tr.get("prompt")) and got_steps == want_steps
        if whole:
            we["prompt"] = we_tr["prompt"]
            if want_steps:
                we["steps"] = we_tr["steps"]
        else:
            missing.append("worked_example")

    if tr.get("recap"):
        out["recap"] = tr["recap"]
    elif out.get("recap"):
        missing.append("recap")

    # Items: prompts and option text only. The correct answer is an index and is
    # never touched, so a translation cannot change what is right.
    item_tr = tr.get("items") or {}
    for item in _all_items(out):
        it = item_tr.get(item.get("id", "")) or {}
        if it.get("prompt"):
            item["prompt"] = it["prompt"]
        else:
            missing.append(f"item {item.get('id')} prompt")
            continue
        if item.get("type") == "mcq" and it.get("options"):
            if len(it["options"]) == len(item.get("options", [])):
                item["options"] = it["options"]
            else:
                missing.append(f"item {item.get('id')} options (count mismatch)")
        if item.get("type") == "short":
            if it.get("answer"):
                item["answer"] = it["answer"]
            else:
                # Marking would be wrong in this language. Keep it English and
                # say so rather than failing the student on vocabulary.
                item["prompt"] = lesson_item_prompt(lesson, item.get("id", "")) or item["prompt"]
                item["_language"] = "en"
                missing.append(f"item {item.get('id')} answer terms")
        if it.get("hint"):
            item["hint"] = it["hint"]
        if it.get("explanation"):
            item["explanation"] = it["explanation"]

    misc_tr = tr.get("misconceptions") or {}
    for m in out.get("misconceptions", []):
        mt = misc_tr.get(m.get("id", "")) or {}
        for field in ("signal", "repair"):
            if mt.get(field):
                m[field] = mt[field]
            elif m.get(field):
                missing.append(f"misconception {m.get('id')} {field}")

    audio = tr.get("audio") or {}
    if audio:
        out["_audio"] = audio

    out["_language"] = language
    out["_untranslated"] = missing
    out["_reviewed"] = is_reviewed(pack_data, language)
    return out


def lesson_item_prompt(lesson: dict[str, Any], item_id: str) -> str | None:
    for item in _all_items(lesson):
        if item.get("id") == item_id:
            return item.get("prompt")
    return None


def _all_items(lesson: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seg in lesson.get("segments", []):
        if seg.get("check"):
            out.append(seg["check"])
    out.extend(lesson.get("practice", []))
    if lesson.get("apply"):
        out.append(lesson["apply"])
    return out


def completeness(pack_data: dict[str, Any], language: str) -> dict[str, Any]:
    """How much of this pack exists in `language`, and what is missing.

    Counted in translatable units rather than characters so the number means
    something to whoever has to finish the work.
    """
    lessons = pack_data.get("lessons", [])
    total = 0
    done = 0
    gaps: list[str] = []

    for lesson in lessons:
        lid = lesson.get("id", "")
        tr = _tr_lesson(pack_data, language, lid)
        for field in ("title", "hook"):
            if lesson.get(field):
                total += 1
                if tr.get(field):
                    done += 1
                else:
                    gaps.append(f"{lid}.{field}")
        for seg in lesson.get("segments", []):
            st = (tr.get("segments") or {}).get(seg.get("id", "")) or {}
            for field in ("heading", "body"):
                if seg.get(field):
                    total += 1
                    if st.get(field):
                        done += 1
                    else:
                        gaps.append(f"{lid}.{seg.get('id')}.{field}")
        if lesson.get("recap"):
            total += 1
            done += 1 if tr.get("recap") else 0
            if not tr.get("recap"):
                gaps.append(f"{lid}.recap")
        if lesson.get("worked_example"):
            total += 1
            done += 1 if (tr.get("worked_example") or {}).get("prompt") else 0
            if not (tr.get("worked_example") or {}).get("prompt"):
                gaps.append(f"{lid}.worked_example")
        for item in _all_items(lesson):
            total += 1
            if ((tr.get("items") or {}).get(item.get("id", "")) or {}).get("prompt"):
                done += 1
            else:
                gaps.append(f"{lid}.item.{item.get('id')}")

    p = provenance(pack_data, language)
    return {
        "language": language,
        "units_total": total,
        "units_done": done,
        "percent": round(100 * done / total) if total else 0,
        "complete": total > 0 and done == total,
        "reviewed": bool((p or {}).get("reviewed_by")),
        "translator": (p or {}).get("translator"),
        "reviewed_by": (p or {}).get("reviewed_by"),
        "method": (p or {}).get("method"),
        "gaps": gaps[:40],
        "gaps_total": len(gaps),
    }
