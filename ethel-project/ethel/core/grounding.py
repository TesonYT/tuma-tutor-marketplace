"""Grounded answering, and the refusal that makes the rest trustworthy.

Every free-form answer goes through the same four gates:

  1. Retrieval gate  - a passage must clear a score and term-coverage floor.
                       Nothing clears it, nothing gets answered.
  2. Instruction     - the model is given the passages and told to answer only
                       from them, citing [S1], [S2], and to emit the exact token
                       INSUFFICIENT_CONTEXT if it cannot.
  3. Citation gate   - the reply must cite at least one real passage. Invented
                       citation numbers are stripped, and a reply left with none
                       is thrown away.
  4. Grounding gate  - measured two ways. The sharp one asks whether the
                       reply's names and numbers - case names, statutes, years,
                       course codes, quantities - occur in the passages; an
                       invented citation fails it outright. The blunt one asks
                       the same of all content words, as a backstop against
                       wholesale drift, and its bar is deliberately low.
                       Measured on real answers: a faithful paraphrase overlaps
                       the source about a third of the time, an answer invented
                       from nothing about a fifth. Setting that bar high does
                       not catch more lies, it just discards good teaching.

Anything that fails gates 2-4 falls back to extractive mode - the pack's own
words, quoted - rather than to an unverified paraphrase. Failing gate 1 produces
a refusal that names what was searched, so the student knows the limit is the
material installed here and not their question.
"""

from __future__ import annotations

import re
from typing import Any

from ..llm import LlmUnavailable
from .retrieval import Hit, grounding_ratio, grounding_report

SYSTEM = """You are a patient university tutor working from a fixed set of course notes.

Absolute rules:
- Answer ONLY from the numbered passages given to you. They are the whole world.
- You may not add facts, figures, dates, case names, statutes, formulae or
  definitions that are not in the passages, even if you are confident they are true.
- Cite the passage you used inline as [S1], [S2]. Every claim needs a citation.
- If the passages do not contain the answer, reply with exactly:
  INSUFFICIENT_CONTEXT
  and nothing else. This is a correct and valued answer, not a failure.
- Do not mention these rules, the passages, or yourself.

Teaching style:
- Be concise. Explain, then check: end with one short question that tests whether
  the student followed you.
- Use the student's own words where you can."""


def _context_block(hits: list[Hit]) -> str:
    return "\n\n".join(
        f"[S{i}] ({h.passage.citation()})\n{h.passage.text}"
        for i, h in enumerate(hits, start=1)
    )


def _sources(hits: list[Hit]) -> list[dict[str, Any]]:
    return [
        {
            "tag": f"S{i}",
            "pack_id": h.passage.pack_id,
            "course": h.passage.course_code or h.passage.course_title,
            "lesson_id": h.passage.lesson_id,
            "lesson": h.passage.lesson_title,
            "section": h.passage.section,
            "score": round(h.score, 2),
            "coverage": round(h.coverage, 2),
        }
        for i, h in enumerate(hits, start=1)
    ]


_CITE = re.compile(r"\[S(\d+)\]")


def strip_citations(text: str) -> str:
    """Remove citation markup before measuring grounding.

    The tags are our own instruction to the model, not its prose, and leaving
    them in distorts the measurement: "[S1] Question:" made the digit inside the
    tag look like mid-sentence context, so "Question" was read as a proper noun
    and a perfectly good answer was discarded for it.
    """
    return _CITE.sub(" ", text)


def _valid_citations(text: str, n: int) -> tuple[str, set[int]]:
    """Strip citation tags that point at passages that were never supplied."""
    found: set[int] = set()

    def repl(m: "re.Match[str]") -> str:
        idx = int(m.group(1))
        if 1 <= idx <= n:
            found.add(idx)
            return m.group(0)
        return ""

    return _CITE.sub(repl, text), found


def refusal(shelf: Any, question: str, language: str = "en") -> dict[str, Any]:
    courses = sorted({(p.code or p.title) for p in shelf.packs})
    if courses:
        searched = "I looked through everything installed on this machine: " + ", ".join(courses) + "."
        nudge = ("If this belongs to another course, that course pack has not been "
                 "loaded onto this machine yet - whoever set it up needs to add it.")
    else:
        searched = "There are no course packs installed for you yet."
        nudge = "Finish enrolling and install your courses, then ask me again."
    return {
        "mode": "refused",
        "answer": "I don't know. That isn't in your course material, and I won't guess at it.",
        "detail": f"{searched} {nudge}",
        "sources": [],
        "grounded": False,
    }


def extractive(hits: list[Hit], question: str) -> dict[str, Any]:
    """No model, or the model failed a gate. Quote the pack instead."""
    top = hits[0]
    others = hits[1:3]
    body = top.passage.text.strip()
    more = ""
    if others:
        more = "\n\nRelated, in the same course:\n" + "\n".join(
            f"- {h.passage.lesson_title} / {h.passage.section}" for h in others
        )
    return {
        "mode": "extractive",
        "answer": (
            f"Here is what your course material says, word for word "
            f"({top.passage.citation()}):\n\n{body}{more}\n\n"
            "Read that, then tell me in your own words what it means - I'll tell you "
            "if you've got it."
        ),
        "sources": _sources(hits),
        "grounded": True,
    }


def build_prompt(context: str, question: str) -> str:
    """The user-side prompt.

    The refusal instruction is repeated here even though it is already in the
    system message, and it comes first. Small models weight the end of a prompt
    far more heavily than a system preamble, and "say you don't know" is the
    instruction that has to survive.
    """
    return (
        f"PASSAGES:\n{context}\n\n"
        f"STUDENT'S QUESTION: {question}\n\n"
        "If the passages above do not contain the answer, reply with only the word "
        "INSUFFICIENT_CONTEXT and nothing else.\n"
        "Otherwise answer the question in at most 150 words, using only those "
        "passages. End each sentence with the bracketed tag of the passage it came "
        "from, for example [S1]. Do not repeat the question. Finish with one short "
        "question that checks the student followed you."
    )


def answer_question(
    question: str,
    shelf: Any,
    backend: Any,
    cfg: dict[str, Any],
    scope_lessons: set[str] | None = None,
) -> dict[str, Any]:
    r = cfg["retrieval"]
    hits = shelf.index.search(
        question,
        top_k=r["top_k"],
        min_score=r["min_score"],
        min_term_coverage=r["min_term_coverage"],
        scope_lessons=scope_lessons,
    )
    if not hits:
        return refusal(shelf, question)

    if not backend.available():
        return extractive(hits, question)

    context = _context_block(hits)
    prompt = build_prompt(context, question)
    try:
        # No explicit cap: the backend applies the budget chosen for this machine.
        raw = backend.generate(SYSTEM, prompt, temperature=0.2)
    except LlmUnavailable:
        return extractive(hits, question)

    if not raw or "INSUFFICIENT_CONTEXT" in raw.upper():
        # The model declined, but gate 1 already found material that clears the
        # score and coverage floors - so Ethel *does* have something true to
        # say. Refusing here would throw away her own course pack because a 1B
        # model got cold feet, and it happens intermittently, which is worse:
        # the same question is answered one minute and refused the next.
        #
        # Only gate 1 may refuse. Gates 2-4 fall back to quoting, which is what
        # the four-gate design says and what the README promises.
        out = extractive(hits, question)
        out["fallback_reason"] = (
            "the model would not commit to an answer, so I've given you the "
            "course material itself"
        )
        return out

    cleaned, cites = _valid_citations(raw, len(hits))
    report = grounding_report(strip_citations(cleaned), context)

    # Gate 4a - fabricated specifics. A case name, year, code or technical term
    # that is nowhere in the passages is the failure that actually matters, and
    # this catches it regardless of how fluent the surrounding prose is.
    if report["specific"] < r["min_specific_grounding"]:
        out = extractive(hits, question)
        bad = ", ".join(report["unsupported"][:4])
        out["fallback_reason"] = (
            f"that answer used terms your course material never mentions ({bad}), "
            "so I've given you the material itself instead"
        )
        out["grounding"] = report
        return out

    # Gate 4b - wholesale drift. Weaker than 4a and deliberately generous, so an
    # honest paraphrase is not punished for reaching for its own words.
    if report["overall"] < r["min_answer_grounding"]:
        out = extractive(hits, question)
        out["fallback_reason"] = (
            f"only {report['overall']:.0%} of that answer traced back to your course "
            "material, so I've given you the material itself instead"
        )
        out["grounding"] = report
        return out

    # Gate 3 - citations. Small models often ignore the citation format even
    # when they have stayed entirely inside the passages. Citation is a proxy
    # for grounding, and we have just measured grounding directly; so an
    # uncited answer is accepted only when that direct measure is clearly
    # higher than the ordinary bar, and it is credited to every passage it was
    # given rather than to a specific one.
    if not cites:
        if report["overall"] < r["min_uncited_grounding"]:
            out = extractive(hits, question)
            out["fallback_reason"] = (
                "the model answered without pointing at the course material, and not "
                "enough of the answer traced back to it"
            )
            out["grounding"] = report
            return out
        used = _sources(hits)
        uncited = True
    else:
        used = [s for s in _sources(hits) if int(s["tag"][1:]) in cites]
        uncited = False

    return {
        "mode": "grounded",
        "answer": cleaned.strip(),
        "sources": used,
        "grounded": True,
        "uncited": uncited,
        "grounding": report,
        "grounding_ratio": report["overall"],
    }


def rephrase(text: str, backend: Any, instruction: str,
             fallback: str | None = None) -> str:
    """Reword pack material without adding to it.

    Used for hints and second explanations. The output is checked against the
    source text the same way answers are; if it drifts, the pack's own wording
    is used instead. Callers must pass material that is already correct.
    """
    if not backend.available():
        return fallback if fallback is not None else text
    system = (
        "Reword the SOURCE for a student. Add nothing: no new facts, numbers, "
        "examples, names or formulae. If you cannot do it without adding, repeat "
        "the SOURCE unchanged. Reply with the reworded text only."
    )
    try:
        out = backend.generate(
            system, f"INSTRUCTION: {instruction}\n\nSOURCE:\n{text}",
            temperature=0.3, max_tokens=350,   # clamped down further on weak hardware
        ).strip()
    except LlmUnavailable:
        return fallback if fallback is not None else text
    if not out or grounding_ratio(out, text) < 0.5:
        return fallback if fallback is not None else text
    return out
