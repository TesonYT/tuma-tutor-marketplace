"""The teaching engine.

The difference between a tutor and a search box is that a tutor does not let you
move on. This module builds an explicit, inspectable *plan of moves* for a
lesson and then refuses to advance past a checkpoint until the student has
actually answered it.

A plan looks like:

    hook -> segment 0 -> check 0 -> segment 1 -> check 1 -> worked example
         -> practice 0 -> practice 1 -> practice 2 -> apply -> recap

The plan is shaped by the learner profile from placement: a "scaffolded" student
gets micro-recaps and every check; a "fast" student who already tested strong on
the skill gets the hook and the worked example dropped; an "example first"
student gets the worked example moved ahead of the explanation.

When an answer is wrong, the engine looks up the pack's own diagnosis for that
specific wrong answer and *injects* a remediation move plus a second attempt at
the same idea. It does not simply reveal the answer. If a student fails the
mastery gate at the end of the lesson, extra practice is injected rather than
the lesson being marked complete.

The language model is never the source of a fact here. It only rewords material
that is already in the pack (see `grounding.rephrase`).
"""

from __future__ import annotations

import time
from typing import Any

from . import grounding, profile as profile_mod
from .grading import grade


def lesson_key(pack_id: str, lesson_id: str) -> str:
    return f"{pack_id}:{lesson_id}"


# --- planning --------------------------------------------------------------

def build_plan(lesson: dict[str, Any], record: dict[str, Any],
               cfg: dict[str, Any]) -> list[dict[str, Any]]:
    prefs = record.get("preferences", {})
    pace = prefs.get("pace", "standard")
    skill = lesson.get("skill") or ""
    known = profile_mod.mastery_of(record, skill)
    strong = known >= cfg["pedagogy"]["mastery_threshold"]

    segments = lesson.get("segments", [])
    practice = lesson.get("practice", [])
    plan: list[dict[str, Any]] = []

    if lesson.get("hook") and not (pace == "fast" and strong):
        plan.append({"type": "hook"})

    worked = {"type": "worked_example"} if lesson.get("worked_example") else None
    if worked and prefs.get("example_first", True):
        plan.append(worked)
        worked = None

    for i, seg in enumerate(segments):
        plan.append({"type": "segment", "i": i})
        has_check = bool(seg.get("check"))
        # A fast learner who already has the skill still gets checked, just not
        # after every single segment.
        skip = pace == "fast" and strong and i % 2 == 1
        if has_check and not skip:
            plan.append({"type": "check", "i": i})
        if pace == "scaffolded" and i and i < len(segments) - 1:
            plan.append({"type": "micro_recap", "i": i})

    if worked:
        plan.append(worked)

    n_practice = len(practice)
    if pace == "fast" and strong:
        n_practice = min(n_practice, max(1, n_practice // 2))
    elif pace == "scaffolded":
        n_practice = len(practice)
    for i in range(n_practice):
        plan.append({"type": "practice", "i": i})

    if lesson.get("apply"):
        plan.append({"type": "apply"})
    if lesson.get("recap"):
        plan.append({"type": "recap"})
    plan.append({"type": "end"})
    return plan


def start(record: dict[str, Any], shelf: Any, pack_id: str, lesson_id: str,
          cfg: dict[str, Any], restart: bool = False) -> dict[str, Any]:
    pack, lesson = shelf.lesson(pack_id, lesson_id)
    if lesson is None:
        raise KeyError(f"{pack_id}:{lesson_id}")
    key = lesson_key(pack_id, lesson_id)
    states = record.setdefault("lessons", {})
    state = states.get(key)
    if state is None or restart or state.get("state") == "done":
        state = {
            "pack_id": pack_id,
            "lesson_id": lesson_id,
            "skill": lesson.get("skill"),
            "plan": build_plan(lesson, record, cfg),
            "cursor": 0,
            "asked": 0,
            "right": 0,
            "wrong_streak": 0,
            "remediations": 0,
            "state": "in_progress",
            "started": time.time(),
        }
        states[key] = state
    record["active_lesson"] = {"pack_id": pack_id, "lesson_id": lesson_id}
    return state


def _state(record: dict[str, Any]) -> dict[str, Any] | None:
    active = record.get("active_lesson")
    if not active:
        return None
    return record.get("lessons", {}).get(
        lesson_key(active["pack_id"], active["lesson_id"])
    )


# --- rendering one move ----------------------------------------------------

def _item_for_client(item: dict[str, Any]) -> dict[str, Any]:
    """Never send the answer key to the browser."""
    return {
        "id": item.get("id"),
        "type": item.get("type", "mcq"),
        "prompt": item.get("prompt", ""),
        "options": item.get("options"),
        "placeholder": item.get("placeholder"),
        "skill": item.get("skill"),
        "unit": item.get("unit"),
    }


def _simplify(text: str, record: dict[str, Any], backend: Any) -> str:
    if not record.get("preferences", {}).get("reading_support"):
        return text
    return grounding.rephrase(
        text, backend,
        "Rewrite in short, plain sentences for a student reading in a second "
        "language. Keep every technical term, but define each one the first time "
        "it appears using only what the source already says.",
        fallback=text,
    )


def render(record: dict[str, Any], shelf: Any, backend: Any,
           cfg: dict[str, Any]) -> dict[str, Any]:
    state = _state(record)
    if state is None:
        return {"kind": "idle"}
    pack, lesson = shelf.lesson(state["pack_id"], state["lesson_id"])
    if lesson is None:
        return {"kind": "idle"}

    plan = state["plan"]
    cursor = min(state["cursor"], len(plan) - 1)
    move = plan[cursor]
    progress = {
        "step": cursor + 1,
        "total": len(plan),
        "asked": state["asked"],
        "right": state["right"],
        "lesson": lesson["title"],
        "course": pack.code or pack.title,
    }
    # Which pieces of this lesson are not really in the student's language.
    # Carried onto the step so the interface can mark them rather than let a
    # student assume a person checked every word in front of them.
    gaps = set(lesson.get("_untranslated") or [])
    _whole = "whole lesson" in gaps

    def _untr(key: str) -> dict[str, Any]:
        """Whether this particular passage is really in the student's language.

        Two different situations, and a student needs to be able to tell them
        apart: nobody has translated this course at all, or this course is
        translated but this passage is not finished yet.
        """
        if (record.get("preferences") or {}).get("language", "en") == "en":
            return {}
        if _whole:
            return {"untranslated": True,
                    "untranslated_note": "This course has not been translated "
                                         "into your language yet, so it is shown "
                                         "in English."}
        return {"untranslated": key in gaps}
    base = {
        "pack_id": state["pack_id"],
        "lesson_id": state["lesson_id"],
        "language": lesson.get("_language") or "en",
        "progress": progress,
        "move": move["type"],
    }
    src = [{"course": pack.code or pack.title, "lesson": lesson["title"]}]

    t = move["type"]

    if t == "hook":
        return {**base, "kind": "say", "title": "Why this matters",
                "body": _simplify(lesson.get("hook", ""), record, backend),
                "audio_key": "hook",
                **_untr("hook"),
                "sources": src}

    if t == "segment":
        seg = lesson["segments"][move["i"]]
        return {**base, "kind": "say",
                "title": seg.get("heading") or lesson["title"],
                "body": _simplify(seg.get("body", ""), record, backend),
                "audio_key": f"segment:{seg.get('id')}",
                **_untr(f"segment {seg.get('id')} body"),
                "sources": src}

    if t == "micro_recap":
        seg = lesson["segments"][move["i"]]
        return {**base, "kind": "say", "title": "Quick recap",
                "body": seg.get("recap") or "So far: " + (seg.get("heading") or ""),
                "sources": src}

    if t == "check":
        seg = lesson["segments"][move["i"]]
        return {**base, "kind": "ask", "title": "Check you've got it",
                "item": _item_for_client(seg["check"]), "sources": src}

    if t == "worked_example":
        we = lesson["worked_example"]
        body = we.get("prompt", "") + "\n\n" + "\n".join(
            f"{i}. {s}" for i, s in enumerate(we.get("steps", []), start=1)
        )
        return {**base, "kind": "say", "title": "Worked example",
                "body": body, "sources": src}

    if t == "practice":
        item = lesson["practice"][move["i"]]
        return {**base, "kind": "ask", "title": f"Practice {move['i'] + 1}",
                "item": _item_for_client(item), "sources": src}

    if t == "extra_practice":
        item = move["item"]
        return {**base, "kind": "ask", "title": "One more, same idea",
                "item": _item_for_client(item), "sources": src}

    if t == "remediate":
        return {**base, "kind": "say", "title": "Let's fix that",
                "body": move["body"], "sources": src}

    if t == "retry":
        return {**base, "kind": "ask", "title": "Try that again",
                "item": _item_for_client(move["item"]), "sources": src}

    if t == "apply":
        ap = lesson["apply"]
        return {**base, "kind": "ask", "title": "Use it",
                "item": _item_for_client(ap), "sources": src}

    if t == "recap":
        points = lesson.get("recap", [])
        return {**base, "kind": "say", "title": "What you now know",
                "body": "\n".join(f"- {p}" for p in points), "sources": src}

    # end
    mastered = _mastery_gate(record, state, cfg)
    return {**base, "kind": "done",
            "title": "Lesson complete" if mastered else "Nearly there",
            "body": _closing_note(state, mastered),
            "mastered": mastered,
            "sources": src}


def _closing_note(state: dict[str, Any], mastered: bool) -> str:
    asked, right = state["asked"], state["right"]
    score = f"You answered {right} of {asked} questions correctly."
    if mastered:
        return (f"{score} That clears the mastery bar, so this lesson is done and "
                "I've scheduled a short review of it for you.")
    return (f"{score} That's below the bar I use before saying you've got it, so I've "
            "added more practice on the same idea rather than moving you on.")


def _mastery_gate(record: dict[str, Any], state: dict[str, Any],
                  cfg: dict[str, Any]) -> bool:
    if state["asked"] == 0:
        return False
    ratio = state["right"] / state["asked"]
    return ratio >= cfg["pedagogy"]["mastery_threshold"]


# --- advancing -------------------------------------------------------------

def _current_item(lesson: dict[str, Any], move: dict[str, Any]) -> dict[str, Any] | None:
    t = move["type"]
    if t == "check":
        return lesson["segments"][move["i"]].get("check")
    if t == "practice":
        return lesson["practice"][move["i"]]
    if t == "apply":
        return lesson.get("apply")
    if t in ("retry", "extra_practice"):
        return move["item"]
    return None


def advance(record: dict[str, Any], shelf: Any, backend: Any, cfg: dict[str, Any],
            response: Any = None) -> dict[str, Any]:
    """Consume the student's response to the current move and move on.

    Returns a feedback block plus the next thing to render.
    """
    state = _state(record)
    if state is None:
        return {"kind": "idle"}
    pack, lesson = shelf.lesson(state["pack_id"], state["lesson_id"])
    plan = state["plan"]
    cursor = min(state["cursor"], len(plan) - 1)
    move = plan[cursor]
    item = _current_item(lesson, move)
    feedback: dict[str, Any] | None = None

    if item is not None:
        result = grade(item, response)
        skill = item.get("skill") or lesson.get("skill") or ""
        difficulty = int(item.get("difficulty", 3))
        state["asked"] += 1
        state["right"] += 1 if result["correct"] else 0
        profile_mod.observe(record, skill, result["correct"], difficulty)
        profile_mod.schedule_review(
            record, f"{state['pack_id']}:{state['lesson_id']}:{item.get('id','?')}",
            result["correct"],
        )

        if result["correct"]:
            state["wrong_streak"] = 0
            feedback = {
                "correct": True,
                "message": _praise(state),
                "explanation": item.get("explanation", ""),
            }
        else:
            state["wrong_streak"] += 1
            feedback = _handle_wrong(record, state, lesson, move, item, result,
                                     backend, cursor)

    state["cursor"] = min(cursor + 1, len(plan) - 1)

    # Mastery gate: at the end of the plan, inject extra practice instead of
    # signing the student off on a lesson they have not demonstrated.
    if plan[state["cursor"]]["type"] == "end":
        if not _mastery_gate(record, state, cfg) and state["remediations"] < 3:
            extra = _spare_items(lesson, state)
            if extra:
                state["remediations"] += 1
                plan[state["cursor"]:state["cursor"]] = [
                    {"type": "remediate",
                     "body": "Before we finish, two more on the part that tripped you up."},
                    {"type": "extra_practice", "item": extra[0]},
                ]
        else:
            state["state"] = "done"
            state["completed"] = time.time()
            profile_mod.note(record, "lesson_done",
                             f"{state['pack_id']}:{state['lesson_id']} "
                             f"{state['right']}/{state['asked']}")

    nxt = render(record, shelf, backend, cfg)
    if feedback:
        nxt["feedback"] = feedback
    return nxt


def _praise(state: dict[str, Any]) -> str:
    streak = state["right"]
    if streak and streak % 3 == 0:
        return "Three in a row - you've got this one."
    return "Correct."


def _handle_wrong(record: dict[str, Any], state: dict[str, Any],
                  lesson: dict[str, Any], move: dict[str, Any],
                  item: dict[str, Any], result: dict[str, Any],
                  backend: Any, cursor: int) -> dict[str, Any]:
    """Diagnose, repair, and re-ask - do not just reveal the answer."""
    plan = state["plan"]
    mid = result.get("misconception")
    repair = None
    if mid:
        m = next((x for x in lesson.get("misconceptions", []) if x["id"] == mid), None)
        if m:
            repair = m.get("repair")

    message = result.get("detail") or "Not quite."
    inject: list[dict[str, Any]] = []

    if repair and state["remediations"] < 4 and move["type"] != "retry":
        state["remediations"] += 1
        inject = [
            {"type": "remediate", "body": repair},
            {"type": "retry", "item": _variant(item)},
        ]
        explanation = ""
    elif move["type"] == "retry" or state["wrong_streak"] >= 2:
        # Second miss on the same idea: stop drilling, give the pack's
        # explanation in full, and move on rather than grinding the student down.
        explanation = item.get("explanation", "")
        hint = item.get("hint")
        if hint and not explanation:
            explanation = hint
    else:
        hint = item.get("hint")
        if hint:
            state["remediations"] += 1
            inject = [
                {"type": "remediate", "body": f"Here's a nudge: {hint}"},
                {"type": "retry", "item": _variant(item)},
            ]
            explanation = ""
        else:
            explanation = item.get("explanation", "")

    if inject:
        plan[cursor + 1:cursor + 1] = inject

    return {
        "correct": False,
        "message": message,
        "explanation": grounding.rephrase(
            explanation, backend,
            "Explain this to a student who just got the question wrong. Be kind and brief.",
            fallback=explanation,
        ) if explanation else "",
        "retrying": bool(inject),
    }


def _variant(item: dict[str, Any]) -> dict[str, Any]:
    """A second attempt at the same item.

    Packs may supply a genuine `variant`. Where they don't, the same item is
    re-asked with its options shuffled - deliberately not a model-generated
    question, because a generated question has no verified answer key.
    """
    if item.get("variant"):
        return dict(item["variant"], id=f"{item.get('id','v')}-v", skill=item.get("skill"),
                    difficulty=item.get("difficulty", 3))
    clone = dict(item)
    clone["id"] = f"{item.get('id', 'i')}-retry"
    if clone.get("type", "mcq") == "mcq" and clone.get("options"):
        order = list(range(len(clone["options"])))
        order = order[1:] + order[:1]
        clone["options"] = [item["options"][i] for i in order]
        clone["answer"] = order.index(int(item["answer"]))
        if item.get("misconception"):
            clone["misconception"] = {
                str(order.index(int(k))): v
                for k, v in item["misconception"].items() if k.isdigit()
            }
    return clone


def _spare_items(lesson: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    used = {m.get("i") for m in state["plan"] if m["type"] == "practice"}
    spare = [p for i, p in enumerate(lesson.get("practice", [])) if i not in used]
    if spare:
        return spare
    # Nothing left in the pack: re-ask the hardest item as a variant.
    practice = sorted(lesson.get("practice", []),
                      key=lambda p: int(p.get("difficulty", 3)), reverse=True)
    return [_variant(practice[0])] if practice else []


# --- course-level planning -------------------------------------------------

def study_plan(record: dict[str, Any], shelf: Any, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Order the installed lessons for this student.

    Prerequisites first, then anything the placement test flagged as a gap, then
    the rest. Lessons whose skill the student already demonstrated are marked
    `review` rather than removed - the tutor still checks, it just doesn't teach
    from scratch.
    """
    gaps = set((record.get("placement", {}).get("summary", {}) or {}).get("gaps", []))
    rows = []
    for lesson in shelf.all_lessons():
        key = lesson_key(lesson["pack_id"], lesson["lesson_id"])
        state = record.get("lessons", {}).get(key, {})
        skill = lesson.get("skill") or ""
        mastered = profile_mod.has_mastered(record, skill, cfg)
        blocked = [
            p for p in lesson.get("prerequisites", [])
            if not profile_mod.has_mastered(record, p, cfg)
        ]
        rows.append({
            **lesson,
            "key": key,
            "status": state.get("state", "not_started"),
            "score": (state.get("right"), state.get("asked")) if state else None,
            "mastery": round(profile_mod.mastery_of(record, skill), 2),
            "mode": "review" if mastered else "teach",
            "priority": (0 if skill in gaps else 1, 0 if not blocked else 1),
            "blocked_by": blocked,
        })
    rows.sort(key=lambda r: (r["priority"], r["course_code"], r["lesson_id"]))
    for r in rows:
        r.pop("priority", None)
    return rows
