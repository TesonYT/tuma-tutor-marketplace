"""Socratic mode: ask before telling, one line at a time.

Ordinary lesson mode presents a segment and then checks it. That works, but a
student can read a paragraph, nod along, and discover at the question that they
followed none of it. Socratic mode inverts the order. It takes the segment apart
into single sentences and, for each one, asks the student to say what they think
before showing them what the pack says.

The pedagogy is deliberately old-fashioned: the value is in the attempt, not in
the marking. A student who guesses wrongly and then sees the sentence has
engaged with the idea; a student who read the sentence first has not.

**No model is involved, and that is the point.** The lines come from the pack.
The probes are built from the pack's own sentence structure. The judgement is
lexical - the same arithmetic that marks everything else here - so it cannot
flatter a wrong answer to be agreeable, it works with no model installed, and it
returns in milliseconds instead of the ten to thirty seconds a small language
model needs on modest hardware.

That last property is what makes a spoken version usable. A voice loop is only
as fast as its slowest leg; keeping the model out leaves text-to-speech and
recognition, which on a 2-core machine measure about four seconds a turn. Put a
model in the loop and the same turn takes half a minute, which is not a
conversation.

What the model *may* do, if one is installed, is reword the pack's sentence when
a student asks for it again - the existing `grounding.rephrase`, held to the
same checks. It never judges and it never supplies the sentence.
"""

from __future__ import annotations

import re
from typing import Any

from .retrieval import content_terms, stem

# How much of a line's substance the student has to produce before it counts as
# understood. Deliberately not high: this is a conversation, not an exam, and a
# student who names two of three key ideas has followed the sentence.
GRASPED = 0.55
PARTIAL = 0.25

# A line carrying fewer content words than this is a lead-in, not a concept -
# "Two elements do the work.", "The standard categories:", "Three quantities
# describe what is happening in any circuit." Measured across the shipped packs,
# scene-setters carry two to five content terms and real explanatory sentences
# carry eight or more. A lead-in is glued onto the sentence it introduces rather
# than probed on its own, because asking a student to predict a scene-setter
# marks good understanding as wrong.
MIN_CONTENT_TERMS = 6

# Things students say when they would rather be told. Treated as "skipped"
# rather than "missed" - the answer is honest and deserves a gentler reply than
# a wrong guess.
_DONT_KNOW = re.compile(
    r"^\s*(i\s*(do\s*n[o']?t|dont)\s*know|no\s*idea|not\s*sure|dunno|"
    r"no\s*clue|skip|pass|nothing|\?+)\s*[.!]?\s*$", re.I)


def split_lines(text: str) -> list[dict[str, str]]:
    """A paragraph into sentences, without breaking on the obvious traps.

    Splitting on full stops alone cuts 'pH 5.5' and 'v Bindley (1862)' in half,
    which produces nonsense probes. This protects decimals, common abbreviations
    and initials before splitting, then restores them.
    """
    guarded = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", text)
    guarded = re.sub(r"\b([A-Z])\.", r"\1<DOT>", guarded)
    for abbr in ("e.g", "i.e", "cf", "vs", "approx", "Fig", "No"):
        guarded = guarded.replace(f"{abbr}.", f"{abbr}<DOT>")

    parts = [p.replace("<DOT>", ".").strip()
             for p in re.split(r"(?<=[.!?])\s+", guarded)]
    parts = [p for p in parts if p]

    out: list[dict[str, str]] = []
    carry = ""
    for line in parts:
        if len(content_terms(line)) < MIN_CONTENT_TERMS:
            # A lead-in belongs with what it introduces, so carry it forward -
            # but kept separate, because it is context to read, not substance to
            # be marked on. Scoring a student for failing to say "three
            # quantities describe" is noise dressed as feedback.
            carry = f"{carry} {line}".strip()
            continue
        out.append({"lead": carry, "text": line})
        carry = ""
    if carry:
        if out:
            out[-1]["text"] = f"{out[-1]['text']} {carry}"
        else:
            out.append({"lead": "", "text": carry})
    return out


def _key_terms(line: str) -> list[str]:
    """The content words a student would have to produce to show they followed.

    Stemmed and de-duplicated, so 'resists', 'resisting' and 'resistance' count
    once and match each other.
    """
    seen: dict[str, str] = {}
    for term in content_terms(line):
        s = stem(term)
        if len(s) > 2 and s not in seen:
            seen[s] = term
    return list(seen.values())


def _probe_for(line: str, heading: str, index: int) -> str:
    """The question asked *before* the line is shown.

    Varied by position rather than randomly, so a session has a shape: predict,
    then explain, then connect. Nothing here is generated - these are fixed
    forms filled with the pack's own heading.
    """
    forms = [
        "What do you expect comes next, and why?",
        "Say in your own words what you think this part explains.",
        "Before I show you: what would you predict here?",
        "How would you explain this to someone in your class?",
        "What do you think follows from what we just covered?",
    ]
    return forms[index % len(forms)]


def build(lesson: dict[str, Any], segment_id: str | None = None) -> dict[str, Any]:
    """A Socratic session over one segment, or the whole lesson."""
    segments = lesson.get("segments", []) or []
    if segment_id:
        segments = [s for s in segments if s.get("id") == segment_id] or segments[:1]

    steps: list[dict[str, Any]] = []
    for seg in segments:
        heading = seg.get("heading") or "(no heading)"
        for piece in split_lines(seg.get("body", "") or ""):
            # Terms come from the sentence only; the lead-in is shown with it
            # but never scored.
            terms = _key_terms(piece["text"])
            if len(terms) < 2:
                continue                      # nothing substantive to ask about
            shown = f"{piece['lead']} {piece['text']}".strip()
            steps.append({
                "segment_id": seg.get("id"),
                "heading": heading,
                "line": shown,
                "terms": terms,
                "probe": _probe_for(piece["text"], heading, len(steps)),
            })
    # The session opens by activating what the student already believes. That
    # question is deliberately NOT scored: there is no sentence it corresponds
    # to, and judging a good answer against whichever line happens to come first
    # marks real understanding as wrong. It exists to get them thinking, and
    # every answer to it is accepted.
    opener = None
    if steps:
        topic = steps[0]["heading"]
        opener = {
            "heading": topic,
            "probe": (f"We are looking at {topic.rstrip('.')}. What do you already "
                      "think you know about it?"
                      if topic and topic != "(no heading)"
                      else "What do you already think you know about this?"),
        }
    return {
        "lesson_id": lesson.get("id"),
        "lesson_title": lesson.get("title"),
        "segment_id": segment_id,
        "opener": opener,
        "opened": False,
        "steps": steps,
        "i": 0,
        "results": [],
    }


def judge(step: dict[str, Any], answer: str) -> dict[str, Any]:
    """How much of the line's substance the student produced.

    Lexical and stemmed. A student who writes "the soil holds onto the
    phosphorus so the plant can't get it" matches 'phosphorus' and 'soil'
    against a line about phosphate fixation - not perfectly, but honestly, and
    without a model deciding how generous to be.
    """
    said = {stem(t) for t in content_terms(answer or "")}
    wanted = [(t, stem(t)) for t in step["terms"]]
    hit = [t for t, s in wanted if s in said]
    missed = [t for t, s in wanted if s not in said]
    score = len(hit) / len(wanted) if wanted else 0.0

    text = (answer or "").strip()
    if not text or _DONT_KNOW.match(text):
        verdict = "skipped"
    elif score >= GRASPED:
        verdict = "grasped"
    elif score >= PARTIAL:
        verdict = "partial"
    else:
        verdict = "missed"

    return {
        "verdict": verdict,
        "score": round(score, 2),
        "matched": hit,
        "missing": missed[:6],
        # What Ethel says back. Every one of these is a statement about the
        # student's own words or a pointer at the pack - never a new claim.
        "response": {
            "grasped": "That is it. Here is how the material puts it:",
            "partial": "Part of it. You have {hit}; the piece you did not mention is {miss}. The material says:",
            "missed": "Not quite. Read this and tell me which part you had not considered:",
            "skipped": "No matter. Here is what it says, then I will ask you about it:",
        }[verdict].format(
            hit=", ".join(hit[:3]) or "some of it",
            miss=", ".join(missed[:2]) or "the rest",
        ),
    }


def step_view(session: dict[str, Any]) -> dict[str, Any] | None:
    """The current step, without the answer in it."""
    if not session.get("opened") and session.get("opener"):
        return {"index": -1, "total": len(session["steps"]),
                "heading": session["opener"]["heading"],
                "probe": session["opener"]["probe"],
                "segment_id": None, "opener": True}
    if session["i"] >= len(session["steps"]):
        return None
    s = session["steps"][session["i"]]
    return {
        "index": session["i"],
        "total": len(session["steps"]),
        "heading": s["heading"],
        "probe": s["probe"],
        "segment_id": s["segment_id"],
    }


def answer(session: dict[str, Any], text: str) -> dict[str, Any]:
    """Judge the current step and move on. Returns what to show and say."""
    # The opener is acknowledged, never marked.
    if not session.get("opened") and session.get("opener"):
        session["opened"] = True
        said = bool((text or "").strip()) and not _DONT_KNOW.match((text or "").strip())
        return {
            "done": False,
            "verdict": "opener",
            "score": None,
            "response": ("Good - hold on to that, and let us see how the material "
                         "puts it." if said else
                         "That is fine. Let us build it up a line at a time."),
            "line": "",
            "missing": [],
            "heading": session["opener"]["heading"],
            "next": step_view(session),
            "spoken": ("Good. Hold on to that." if said
                       else "That is fine. Let us build it up a line at a time."),
        }
    if session["i"] >= len(session["steps"]):
        return {"done": True}
    step = session["steps"][session["i"]]
    result = judge(step, text)
    session["results"].append({
        "line": step["line"], "verdict": result["verdict"], "score": result["score"],
    })
    session["i"] += 1
    return {
        "done": False,
        "verdict": result["verdict"],
        "score": result["score"],
        "response": result["response"],
        "line": step["line"],
        "missing": result["missing"],
        "heading": step["heading"],
        "next": step_view(session),
        "spoken": f"{result['response']} {step['line']}",
    }


def summary(session: dict[str, Any]) -> dict[str, Any]:
    """What the session showed, in the student's own results."""
    res = session["results"]
    counts = {v: sum(1 for r in res if r["verdict"] == v)
              for v in ("grasped", "partial", "missed", "skipped")}
    weakest = [r["line"] for r in res if r["verdict"] in ("missed", "skipped")][:3]
    done = len(res)
    return {
        "lines": done,
        "counts": counts,
        "grasped_share": round(counts["grasped"] / done, 2) if done else 0.0,
        "revisit": weakest,
        "note": (
            "These are the lines you did not put into your own words. That is "
            "not a mark - it is where to look again."
            if weakest else
            "You put every line into your own words."
        ),
    }
