"""Deterministic grading.

Marking is done by code, never by the language model. A model that grades its
own students will eventually mark a wrong answer right to be agreeable, and a
student who is told they understand something when they don't is worse off than
one who was never taught. Every item therefore carries its own answer key and
is graded arithmetically or lexically.

Item types:
  mcq     - `answer` is the index of the correct option.
  numeric - `answer` is a number; `tolerance` is a relative tolerance (default 2%).
            Units in the response are ignored, so "0.5 A" and "0.5" both pass.
  short   - `answer` is a list of key terms; `min_match` (default: all of them)
            is how many must appear. `forbidden` terms mark it wrong outright.
"""

from __future__ import annotations

import re
from typing import Any

from .retrieval import content_terms

_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

_SI = {
    "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "meg": 1e6, "g": 1e9, "p": 1e-12,
}


def parse_number(text: str) -> float | None:
    """Pull a number out of a free-text response, honouring k/m/u prefixes."""
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    m = _NUM.search(s)
    if not m:
        return None
    try:
        value = float(m.group(0))
    except ValueError:
        return None
    tail = s[m.end():].strip().lower()
    for prefix, mult in sorted(_SI.items(), key=lambda kv: -len(kv[0])):
        if tail.startswith(prefix):
            return value * mult
    return value


def grade(item: dict[str, Any], response: Any) -> dict[str, Any]:
    kind = item.get("type", "mcq")
    result: dict[str, Any] = {
        "correct": False,
        "kind": kind,
        "explanation": item.get("explanation", ""),
        "misconception": None,
        "detail": "",
    }

    if kind == "mcq":
        try:
            chosen = int(response)
        except (TypeError, ValueError):
            result["detail"] = "No option was selected."
            return result
        result["correct"] = chosen == int(item["answer"])
        result["chosen"] = chosen
        if not result["correct"]:
            result["misconception"] = (item.get("misconception") or {}).get(str(chosen))
            options = item.get("options", [])
            if 0 <= chosen < len(options):
                result["detail"] = f"You chose: {options[chosen]}"

    elif kind == "numeric":
        got = parse_number(response)
        if got is None:
            result["detail"] = "I could not find a number in that answer."
            return result
        want = float(item["answer"])
        tol = float(item.get("tolerance", 0.02))
        span = abs(want) * tol if want else max(tol, 1e-9)
        result["correct"] = abs(got - want) <= span
        result["got"] = got
        if not result["correct"]:
            # A factor-of-ten slip is nearly always a unit-prefix error, and
            # packs can attach a specific repair for it.
            ratio = abs(got / want) if want else 0
            if 0.09 < ratio < 0.11 or 9 < ratio < 11:
                result["misconception"] = (item.get("misconception") or {}).get("order_of_magnitude")
                result["detail"] = "That is out by a factor of ten - check your units."
            else:
                result["detail"] = f"You answered {got:g}."

    elif kind == "short":
        terms = set(content_terms(str(response or "")))
        wanted = [str(w).lower() for w in (item.get("answer") or [])]
        hits = [w for w in wanted if set(content_terms(w)) <= terms]
        forbidden = [f for f in (item.get("forbidden") or [])
                     if set(content_terms(str(f))) <= terms]
        need = int(item.get("min_match", len(wanted)))
        result["correct"] = len(hits) >= need and not forbidden
        result["matched"] = hits
        result["missing"] = [w for w in wanted if w not in hits]
        if forbidden:
            result["misconception"] = (item.get("misconception") or {}).get("forbidden")
            result["detail"] = "Part of that answer points the wrong way."
        elif not result["correct"]:
            result["detail"] = f"You covered {len(hits)} of the {need} ideas I was looking for."

    else:
        result["detail"] = f"Unknown item type {kind!r}."

    return result
