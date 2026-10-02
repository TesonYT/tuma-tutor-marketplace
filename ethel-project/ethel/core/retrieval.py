"""Retrieval over installed course packs.

Deliberately dependency-free: a pure-Python BM25 index plus a term-coverage
gate. No embedding model to download, nothing to keep in sync, and it runs in
milliseconds on modest hardware. Exact terminology - "Kirchhoff", "LPR2920",
"consideration" - is exactly what students search for, and BM25 is strong on
exactly that.

The important part is not the ranking, it is the gate. `search()` returns only
passages that clear both a score floor and a term-coverage floor. If nothing
clears them the caller gets an empty list, and the tutor says it does not know.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

_WORD = re.compile(r"[A-Za-z0-9_]+(?:'[A-Za-z]+)?")

STOPWORDS = {
    "a", "about", "an", "and", "any", "are", "as", "at", "be", "been", "but",
    "by", "can", "could", "did", "do", "does", "for", "from", "get", "give",
    "had", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its",
    "just", "me", "my", "of", "on", "or", "please", "so", "some", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "to", "up", "us", "was", "we", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "you", "your",
}


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text or "")]


def content_terms(text: str) -> list[str]:
    return [t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


@dataclass
class Passage:
    """One retrievable chunk of a course pack."""

    id: str
    pack_id: str
    course_code: str
    course_title: str
    lesson_id: str
    lesson_title: str
    section: str
    text: str
    # Which language this chunk is written in. Retrieval is per-language: a
    # question asked in Bemba must not be answered out of English passages that
    # happen to share a proper noun.
    language: str = "en"
    # "lesson" for authored pack material, "reference" for a document an
    # administrator uploaded. A student must always be able to tell which they
    # are reading: one was shaped by a teacher, the other only quoted.
    kind: str = "lesson"
    tokens: list[str] = field(default_factory=list)

    def citation(self) -> str:
        head = self.course_code or self.course_title
        if self.kind == "reference":
            return f"{head} - uploaded reference: {self.lesson_title} / {self.section}"
        return f"{head} - {self.lesson_title} / {self.section}"


@dataclass
class Hit:
    passage: Passage
    score: float
    coverage: float


class Bm25Index:
    """Okapi BM25. k1/b are the usual defaults; nothing exotic here."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.passages: list[Passage] = []
        self._df: Counter[str] = Counter()
        self._tf: list[Counter[str]] = []
        self._len: list[int] = []
        self._avglen = 0.0

    def add(self, passages: Iterable[Passage]) -> None:
        for p in passages:
            p.tokens = tokenize(p.text)
            tf = Counter(p.tokens)
            self.passages.append(p)
            self._tf.append(tf)
            self._len.append(len(p.tokens))
            self._df.update(tf.keys())
        n = len(self._len)
        self._avglen = (sum(self._len) / n) if n else 0.0

    def __len__(self) -> int:
        return len(self.passages)

    def _idf(self, term: str) -> float:
        n = len(self.passages)
        df = self._df.get(term, 0)
        # BM25+ style floor keeps very common terms from going negative.
        return max(math.log(1 + (n - df + 0.5) / (df + 0.5)), 0.0)

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.5,
        min_term_coverage: float = 0.34,
        scope_lessons: set[str] | None = None,
    ) -> list[Hit]:
        q_terms = content_terms(query)
        if not q_terms or not self.passages:
            return []
        q_unique = set(q_terms)

        scored: list[Hit] = []
        for i, p in enumerate(self.passages):
            if scope_lessons is not None and p.lesson_id not in scope_lessons:
                continue
            tf = self._tf[i]
            dl = self._len[i] or 1
            score = 0.0
            for term in q_terms:
                f = tf.get(term, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self._avglen or 1))
                score += self._idf(term) * (f * (self.k1 + 1)) / denom
            if score <= 0:
                continue
            coverage = len(q_unique & set(tf.keys())) / len(q_unique)
            scored.append(Hit(passage=p, score=score, coverage=coverage))

        scored.sort(key=lambda h: (h.score, h.coverage), reverse=True)
        kept = [
            h for h in scored
            if h.score >= min_score and h.coverage >= min_term_coverage
        ]
        return kept[:top_k]


def grounding_ratio(answer: str, context: str) -> float:
    """Fraction of the answer's content words that appear in the context."""
    a = [t for t in content_terms(answer)]
    if not a:
        return 0.0
    ctx = set(content_terms(context))
    return sum(1 for t in a if t in ctx) / len(a)


_SENTENCE_END = {".", "!", "?", "\n", ":", ";", '"', "'"}
# Closers that can sit between the end of a sentence and the next capital:
# "...opposes current [S1].\n\nChecking question:" must not make "Checking" a
# proper noun. Measured: it did, and the answer was discarded for it.
_CLOSERS = set("]})>\"'")


def _starts_sentence(text: str, pos: int) -> bool:
    i = pos - 1
    crossed_line = False
    while i >= 0 and (text[i].isspace() or text[i] in _CLOSERS):
        if text[i] == "\n":
            crossed_line = True
        i -= 1
    return crossed_line or i < 0 or text[i] in _SENTENCE_END


# Words a model capitalises as a structural label rather than as a name.
# "Question:", "Note:", "Step 2" are formatting, not claims about the world, and
# treating them as unverifiable proper nouns discards correct answers. Keep this
# list short and generic - anything domain-specific belongs in a course pack,
# where an unsupported capitalised term is exactly what should be caught.
_STRUCTURAL_CAPS = {
    "question", "questions", "answer", "answers", "note", "notes", "example",
    "examples", "checking", "explanation", "hint", "step", "steps", "summary",
    "recap", "remember", "important", "key", "source", "sources", "passage",
    "passages", "reference", "references", "first", "second", "third", "finally",
}

_SUFFIXES = ("ational", "ization", "iveness", "ities", "ement", "ance", "ence",
             "ings", "edly", "ing", "ies", "ied", "ed", "es", "ly", "s")


def stem(token: str) -> str:
    """Crudest possible stemmer, applied to both sides of every comparison.

    It exists so that a paraphrase saying "rearranged" is not counted as
    unsupported when the source says "rearranges". Precision does not matter
    here; symmetry does.
    """
    t = token
    for _ in range(2):
        for suf in _SUFFIXES:
            if t.endswith(suf) and len(t) - len(suf) >= 4:
                t = t[: -len(suf)]
                break
        else:
            break
    return t


def specific_terms(text: str) -> list[str]:
    """The terms a model would have to *know* rather than infer.

    Exactly two kinds, and they are the two that actually get fabricated:

      - anything containing a digit - years, course codes, quantities, "1893"
      - proper nouns mid-sentence   - case names, statutes, people, places

    An earlier version also treated any long word as specific. Measurement
    killed that: a correct, faithful answer to "what is Ohm's law?" was flagged
    for using "mathematically", "conductor" and "calculate" - ordinary
    vocabulary a good paraphrase reaches for, none of it a claim about the
    world. Names and numbers are different. A model that writes "Hyde v Wrench"
    or "1893" is asserting something checkable, and if the passages do not
    contain it, it came from somewhere the tutor cannot vouch for.
    """
    out: list[str] = []
    for m in _WORD.finditer(text or ""):
        tok = m.group(0)
        low = tok.lower()
        if low in STOPWORDS or len(low) <= 2:
            continue
        if any(ch.isdigit() for ch in tok):
            out.append(low)
        elif (tok[0].isupper()
              and low not in _STRUCTURAL_CAPS
              and not _starts_sentence(text, m.start())):
            out.append(low)
    return out


def grounding_report(answer: str, context: str) -> dict[str, object]:
    """Two measures of how much of an answer traces back to the passages.

    `specific` is the sharp one, and the one that carries the safety property:
    what share of the answer's names and numbers appear in the source. An
    invented case name drops it immediately; a well-written paraphrase leaves
    it at 1.0. `unsupported` names exactly which terms failed, so the fallback
    can tell the student why.

    `overall` is the blunt backstop for wholesale drift. Measured on this
    machine: a correct paraphrase of a passage scores around 0.34, an answer
    invented from nothing around 0.18. The bar sits between them, which is all
    it is good for - it is not a quality measure and must not be read as one.
    """
    ctx = {stem(t) for t in content_terms(context)}

    a_all = [stem(t) for t in content_terms(answer)]
    overall = (sum(1 for t in a_all if t in ctx) / len(a_all)) if a_all else 0.0

    a_spec = specific_terms(answer)
    unsupported = [t for t in a_spec if stem(t) not in ctx]
    # No names or numbers at all means nothing checkable was claimed.
    specific = 1.0 if not a_spec else (len(a_spec) - len(unsupported)) / len(a_spec)

    return {
        "overall": round(overall, 3),
        "specific": round(specific, 3),
        "specific_count": len(a_spec),
        "unsupported": sorted(set(unsupported))[:8],
    }
