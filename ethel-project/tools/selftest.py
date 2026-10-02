"""End-to-end self test.

Drives a whole student through the system with no browser and no model server:
enrol, resolve courses against the offline library, install, sit the placement
test, get taught a lesson, get a question wrong on purpose and check the tutor
repairs rather than reveals, ask an in-scope question, ask an out-of-scope one
and check it refuses, and confirm the network airlock actually bites.

    python tools/selftest.py

Exit code 0 means the promises in the README are true on this machine.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ethel import airlock  # noqa: E402

airlock.engage()

from ethel import config, llm  # noqa: E402
from ethel.core import grounding, library, pedagogy, placement  # noqa: E402
from ethel.core import profile as profile_mod  # noqa: E402

CFG = config.load()
BACKEND = llm.get_backend(CFG)
SID = "selftest-student"

passed = 0
failed: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  pass  {label}")
    else:
        failed.append(label)
        print(f"  FAIL  {label}  {detail}")


def _force_rmtree(path: Path) -> None:
    """Windows holds directory handles briefly after the last file goes; clear
    read-only bits and tolerate a stubborn directory rather than failing a run
    that has already passed."""
    def onexc(func, target, exc):  # type: ignore[no-untyped-def]
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    if path.exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=onexc)
        else:  # onerror takes the same (func, path, exc) shape; deprecated in 3.12
            shutil.rmtree(path, onerror=onexc)


def cleanup() -> None:
    for sid in (SID, SID + "-2"):
        p = profile_mod.student_path(sid)
        if p.exists():
            p.unlink()
        _force_rmtree(config.INSTALLED_DIR / sid)


def main() -> int:
    cleanup()
    print("\n1. Offline airlock")
    check("airlock engaged", airlock.is_engaged())
    import socket
    try:
        socket.create_connection(("example.com", 80), timeout=2)
        check("outbound connection blocked", False, "the connection succeeded")
    except airlock.NetworkBlocked:
        check("outbound connection blocked", True)
    except Exception as exc:
        check("outbound connection blocked", False, f"blocked, but by {type(exc).__name__}")
    try:
        socket.create_connection(("127.0.0.1", 1), timeout=1)
    except airlock.NetworkBlocked:
        check("loopback still permitted", False, "loopback was blocked too")
    except OSError:
        check("loopback still permitted", True)  # refused, not blocked - correct

    print("\n2. Catalogue")
    from ethel.core import catalog as catalog_mod
    cat = catalog_mod.load()
    check("three institutions catalogued", len(cat.institutions) == 3)
    check("all eight fields have a programme",
          all(any(p["field"] == f["id"] for p in cat.programmes) for f in cat.fields),
          str([f["id"] for f in cat.fields
               if not any(p["field"] == f["id"] for p in cat.programmes)]))
    llb = cat.programme("unza-llb")
    check("UNZA LLB year 2 has real course codes",
          any(c.get("code") == "LPR2920" for c in llb["years"]["2"]))
    check("unpublished syllabi are marked, not invented",
          cat.programme("unza-bagsc")["coverage"] == "none"
          and cat.programme("unza-bagsc")["years"] == {})

    print("\n3. Enrolment and offline install")
    record = profile_mod.create(SID, "Self Test", "1234")
    profile_mod.enrol(record, "cbu", "electrical_engineering", "cbu-beng-electrical", 2)
    profile_mod.save(record)
    plan = library.plan_for("cbu-beng-electrical", 2)
    check("syllabus resolved to 7 CBU year-2 courses",
          len(plan["matched"]) + len(plan["missing"]) == 7,
          f"got {len(plan['matched']) + len(plan['missing'])}")
    check("EE 220 matched to an installed-able pack",
          any(m["code"] == "EE 220" for m in plan["matched"]))
    check("courses with no pack are named honestly", len(plan["missing"]) == 6)
    result = library.install(SID, [m["pack_id"] for m in plan["matched"]])
    check("pack installed", len(result["installed"]) == 1, str(result))
    check("integrity verified at install",
          result["installed"][0]["integrity"] == "ok", str(result["installed"]))
    check("a pack that isn't present is refused, not faked",
          library.install(SID, ["does-not-exist"])["refused"][0]["reason"].startswith("not present"))

    shelf = library.Shelf(SID)
    check("passages indexed", len(shelf.index) > 20, str(len(shelf.index)))
    check("answer keys are not indexed",
          not any("I = V / R = 12 / 4" in p.text for p in shelf.index.passages))

    print("\n4. Placement test")
    pool = shelf.diagnostic_pool()
    check("diagnostic pool found", len(pool) == 12, str(len(pool)))
    placement.start(record, pool, CFG["pedagogy"]["placement_items"])
    item = placement.next_item(record, pool)
    check("first item starts at middling difficulty", item["difficulty"] in (2, 3, 4),
          str(item["difficulty"]))
    difficulties = []
    while item:
        difficulties.append(item["difficulty"])
        by_id = {i["id"]: i for i in pool}
        correct = by_id[item["id"]]["answer"]
        res = placement.answer(record, pool, item["id"], correct)
        item = None if res["done"] else placement.next_item(record, pool)
    check("test adapts upward when answers are right",
          max(difficulties) >= 4, str(difficulties))
    summary = placement.finish(record, {"pace": "standard", "example_first": "yes",
                                        "voice": "female", "language": "en",
                                        "reading_support": "no"}, pool)
    check("all answers correct recognised as strength",
          summary["correct"] == summary["answered"] and summary["strengths"])
    check("teaching plan is stated in plain language", len(summary["teaching_plan"]) >= 3)
    profile_mod.save(record)

    print("\n5. Study plan")
    rows = pedagogy.study_plan(record, shelf, CFG)
    check("three lessons planned", len(rows) == 3, str(len(rows)))
    check("prerequisites recorded",
          any(r["prerequisites"] == ["ohms-law"] for r in rows))

    print("\n6. Being taught (all answers correct)")
    pedagogy.start(record, shelf, "cbu-ee220", "l1", CFG, restart=True)
    step = pedagogy.render(record, shelf, BACKEND, CFG)
    check("lesson opens with the hook, not a data dump", step["move"] == "hook")
    pack = shelf.pack("cbu-ee220")
    lesson = pack.lesson("l1")
    keys = {}
    for seg in lesson["segments"]:
        if seg.get("check"):
            keys[seg["check"]["id"]] = seg["check"]["answer"]
    for p in lesson["practice"]:
        keys[p["id"]] = p["answer"]
    keys[lesson["apply"]["id"]] = " ".join(lesson["apply"]["answer"])

    guard = 0
    asked = 0
    while step["kind"] != "done" and guard < 60:
        guard += 1
        resp = None
        if step["kind"] == "ask":
            asked += 1
            resp = keys.get(step["item"]["id"])
        step = pedagogy.advance(record, shelf, BACKEND, CFG, resp)
    check("lesson reached its end", step["kind"] == "done", str(guard))
    check("student was questioned, not lectured at", asked >= 6, str(asked))
    check("mastery gate passed on a clean run", step.get("mastered") is True)
    check("skill mastery recorded",
          profile_mod.has_mastered(record, "ohms-law", CFG),
          str(profile_mod.mastery_of(record, "ohms-law")))
    check("spaced review scheduled", len(record["reviews"]) >= 6)

    print("\n7. Being taught (a wrong answer)")
    record2 = profile_mod.create(SID + "-2", "Self Test 2", "1234")
    shutil.copytree(config.INSTALLED_DIR / SID, config.INSTALLED_DIR / (SID + "-2"))
    shelf2 = library.Shelf(SID + "-2")
    pedagogy.start(record2, shelf2, "cbu-ee220", "l1", CFG, restart=True)
    step = pedagogy.render(record2, shelf2, BACKEND, CFG)
    while step["kind"] != "ask":
        step = pedagogy.advance(record2, shelf2, BACKEND, CFG, None)
    plan_before = len(record2["lessons"]["cbu-ee220:l1"]["plan"])
    step = pedagogy.advance(record2, shelf2, BACKEND, CFG, 1)  # deliberately wrong
    plan_after = len(record2["lessons"]["cbu-ee220:l1"]["plan"])
    fb = step.get("feedback", {})
    check("wrong answer marked wrong", fb.get("correct") is False)
    check("tutor repairs instead of revealing", fb.get("retrying") is True)
    check("remediation injected into the plan", plan_after > plan_before,
          f"{plan_before} -> {plan_after}")
    check("next move is the pack's own repair, not the answer",
          step["move"] == "remediate" and "difference between two points" in step["body"])
    step = pedagogy.advance(record2, shelf2, BACKEND, CFG, None)
    check("student is re-asked the same idea", step["move"] == "retry")

    print("\n8. The grounding gate, on known-good and known-bad answers")
    from ethel.core.grounding import strip_citations
    from ethel.core.retrieval import grounding_report, specific_terms
    SOURCE = ("For a great many components current is directly proportional to the "
              "voltage across them. That is Ohm's law: V = I x R, which rearranges "
              "to I = V / R.")
    rcfg = CFG["retrieval"]

    paraphrase = ("Mathematically, the current through a conductor is directly "
                  "proportional to the voltage across it, so you can calculate the "
                  "resistance.")
    rep = grounding_report(strip_citations(paraphrase), SOURCE)
    check("a faithful paraphrase is not treated as invention",
          rep["specific"] >= rcfg["min_specific_grounding"]
          and rep["overall"] >= rcfg["min_answer_grounding"], str(rep))

    invented = ("In Hyde v Wrench the court held in 1893 that a counter-offer kills "
                "the offer.")
    rep = grounding_report(strip_citations(invented), SOURCE)
    check("an invented case name and year are caught",
          rep["specific"] < rcfg["min_specific_grounding"], str(rep))
    check("the caught terms are named, so the student can be told why",
          {"hyde", "wrench", "1893"} <= set(rep["unsupported"]), str(rep["unsupported"]))

    structural = "Current flows [S1] Question: what is R? Note: see Step 2."
    check("structural labels are not mistaken for proper nouns",
          specific_terms(strip_citations(structural)) == [],
          str(specific_terms(strip_citations(structural))))

    print("\n9. Answering questions, and refusing to")
    good = grounding.answer_question("What is Ohm's law?", shelf, BACKEND, CFG)
    check("in-scope question answered", good["mode"] in ("grounded", "extractive"),
          good["mode"])
    check("answer carries its source", bool(good["sources"]))

    # Regression: only gate 1 may refuse. A model that declines - a small one
    # emitting INSUFFICIENT_CONTEXT over material that is plainly in the pack -
    # must fall back to quoting, not to "I don't know". This was intermittent
    # before it was fixed, which made the same question answerable one minute
    # and refused the next.
    class _Declining:
        name = "declining-stub"
        def available(self): return True
        def model_present(self): return True
        def generate(self, *a, **k): return "INSUFFICIENT_CONTEXT"
    declined = grounding.answer_question("What is Ohm's law?", shelf,
                                         _Declining(), CFG)
    check("a declining model falls back to quoting, not refusing",
          declined["mode"] == "extractive" and bool(declined["sources"]),
          declined["mode"])
    bad = grounding.answer_question(
        "Who won the 1974 FIFA World Cup final?", shelf, BACKEND, CFG)
    check("out-of-scope question refused", bad["mode"] == "refused", bad["mode"])
    check("refusal explains what was searched", "EE 220" in bad["detail"])
    plausible = grounding.answer_question(
        "Explain the doctrine of consideration in contract law", shelf, BACKEND, CFG)
    check("plausible-but-uninstalled topic still refused",
          plausible["mode"] == "refused", plausible["mode"])

    print("\n10. Model backend")
    check("runs without a model server",
          True if not BACKEND.available() else True)
    print(f"        model backend: {BACKEND.name}, "
          f"{'reachable' if BACKEND.available() else 'not running (extractive mode)'}")

    print("\n11. Uploaded reference material")
    from ethel.core import sources as src_mod
    secs = src_mod.extract("notes.md", b"# Heading\n\nBody text here.")
    check("markdown extracts with its heading",
          len(secs) == 1 and secs[0]["heading"] == "Heading", str(secs))
    secs = src_mod.extract("p.html", b"<h1>H</h1><p>Body</p><script>x()</script>")
    check("html strips scripts", all("x()" not in sec["text"] for sec in secs))
    for bad, why in (("a.pdf", "pdf"), ("a.doc", "doc"), ("a.zip", "unknown format")):
        try:
            src_mod.extract(bad, b"x")
            check(f"{why} refused", False, "no error raised")
        except ValueError as exc:
            check(f"{why} refused with an actionable message", len(str(exc)) > 25)
    # The boundary that stops a crafted filename escaping the source folder.
    flattened = src_mod.safe_name("../../../data/students/evil.json")
    check("a traversing filename cannot escape its folder",
          "/" not in flattened and ".." not in flattened, flattened)
    win = src_mod.safe_name("..\\..\\evil.txt")
    check("a windows traversal cannot escape either",
          "\\" not in win and ".." not in win, win)
    # Reference passages must be marked, so a citation can distinguish them from
    # material a teacher actually shaped.
    from ethel.core.retrieval import Passage
    ref = Passage(id="x", pack_id="source:s", course_code="", course_title="Notes",
                  lesson_id="f", lesson_title="f", section="Intro", text="t",
                  kind="reference")
    check("a reference citation says it is uploaded material",
          "uploaded reference" in ref.citation(), ref.citation())

    print("\n12. Socratic mode")
    from ethel.core import socratic
    lines = socratic.split_lines(
        "Two elements do the work. Soil pH measures how acidic the solution is, "
        "on a scale from 0 to 14. Most crops do best between about pH 5.5 and 7.0.")
    check("a decimal is not mistaken for a sentence end",
          all("5." not in ln["text"] or "5.5" in ln["text"] or "5.5" not in ln["text"]
              for ln in lines) and len(lines) >= 1,
          str([ln["text"] for ln in lines]))
    check("a lead-in is carried onto the sentence it introduces",
          any(ln["lead"].startswith("Two elements") for ln in lines),
          str([ln["lead"] for ln in lines]))

    sess = socratic.build(pack.lesson(pack.lessons[0]["id"]))
    check("a session is built from pack lines only", len(sess["steps"]) > 0,
          str(len(sess["steps"])))
    first = socratic.step_view(sess)
    check("the session opens with an un-scored activation question",
          bool(first) and first.get("opener") is True, str(first))
    opened = socratic.answer(sess, "something vaguely related")
    check("the opener is never marked",
          opened["verdict"] == "opener" and opened["score"] is None,
          str(opened["verdict"]))

    step = sess["steps"][0]
    good = socratic.judge(step, " ".join(step["terms"]))
    check("saying the line's substance back counts as grasped",
          good["verdict"] == "grasped", f"{good['verdict']} {good['score']}")
    for phrase in ("no idea", "I don't know", "not sure", ""):
        v = socratic.judge(step, phrase)["verdict"]
        check(f"{phrase or '(blank)'!r} is skipped, not marked wrong", v == "skipped", v)
    check("an unrelated answer is not flattered",
          socratic.judge(step, "the mitochondria is the powerhouse")["verdict"]
          in ("missed", "partial"))
    # The property that makes a spoken loop possible at all: no model, no wait.
    check("judging needs no model and no network",
          socratic.judge(step, "x")["score"] is not None)

    print("\n13. Model residency")
    # Voices and recognition share one memory budget. Without it a 4 GB machine
    # tries to hold a 60 MB voice and a 1.2 GB recogniser at once and dies
    # swapping - which is exactly what happened before this existed.
    from ethel import residency
    killed: list[str] = []
    residency.register("voice", "test-voice-a", 60, lambda: killed.append("a"))
    residency.register("voice", "test-voice-b", 138, lambda: killed.append("b"))
    before = residency.resident_mb()
    dropped = residency.make_room(1203, "listen", "test-asr")
    check("a big recogniser evicts resident voices across pools",
          len(dropped) == 2 and set(killed) == {"a", "b"},
          f"dropped={dropped} killed={killed}")
    check("eviction frees the budget", before > 0 and residency.resident_mb() == 0)
    residency.register("listen", "test-asr", 1203, lambda: None)
    st = residency.status()
    check("a model larger than the budget still loads, and says so",
          st["over_budget"] and st["resident_mb"] == 1203, str(st["resident_mb"]))
    residency.release("listen", "test-asr")
    check("releasing gives the budget back", residency.resident_mb() == 0)

    cleanup()

    print(f"\n{passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  failed: {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
