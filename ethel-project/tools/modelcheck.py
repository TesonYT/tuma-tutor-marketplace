"""Is the local model working, and is it fast enough to teach with?

    python tools/modelcheck.py

Answers three questions in order, and stops at the first honest "no":

  1. Is an Ollama server reachable on loopback, and is the configured model pulled?
  2. Does it obey the grounding contract - answer only from supplied passages,
     cite them, and emit INSUFFICIENT_CONTEXT when the answer is not there?
  3. How long does a real answer take on this machine?

Point 2 matters more than point 3. A fast model that invents a case citation is
worse than no model at all, because Ethel falls back to quoting the pack and the
student still gets something true. This script tries to make the model fail, and
reports whether the gates caught it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ethel import airlock  # noqa: E402

airlock.engage()

from ethel import config, hardware, llm  # noqa: E402
from ethel.core import grounding, library  # noqa: E402
from ethel.core import profile as profile_mod  # noqa: E402

CFG = config.load()

PASSAGES = (
    "[S1] (EE 220 - Ohm's law / Explanation)\n"
    "For a great many components current is directly proportional to the voltage "
    "across them. That is Ohm's law: V = I x R, which rearranges to I = V / R and "
    "R = V / I."
)


def main() -> int:
    print("Ethel - local model check\n")

    p = hardware.profile()
    print(f"machine   : {p['ram_total_gb']} GB RAM ({p['ram_available_gb']} GB free now), "
          f"{p['cores']} cores, tier '{p['tier']}'")
    tuned = CFG["llm"]
    print(f"tuned to  : num_ctx={tuned['num_ctx']} threads={tuned['num_thread']} "
          f"cap={tuned['max_tokens']} timeout={tuned['timeout_s']}s")

    backend = llm.get_backend(CFG)
    if backend.name == "none":
        print("\nbackend is 'none' - Ethel is in extractive mode by configuration.")
        return 0
    if not backend.available():
        print(f"\nFAIL  no Ollama server on {tuned['endpoint']}.")
        print("      Start Ollama, then run this again.")
        return 1
    print(f"server    : reachable, models present: {', '.join(backend.installed_models()) or 'none'}")
    if not backend.model_present():
        print(f"\nFAIL  the configured model '{backend.model}' is not pulled.")
        print(f"      Run:  ollama pull {backend.model}")
        return 1

    if p["ram_available_gb"] and p["ram_available_gb"] < 1.0:
        print(f"\nwarning: only {p['ram_available_gb']} GB of RAM is free. The timings below "
              "will be worse than\n         real use with other programs closed.")

    print("\n1. cold start (first call loads the model into memory)")
    t0 = time.time()
    try:
        backend.generate("Reply with the single word: ready.", "Are you ready?",
                         temperature=0.0, max_tokens=8)
    except llm.LlmUnavailable as exc:
        print(f"   FAIL  {exc}")
        return 1
    cold = time.time() - t0
    print(f"   {cold:.1f}s")

    print("\n2. does it stay inside the passages?")
    t0 = time.time()
    answer = backend.generate(
        grounding.SYSTEM,
        grounding.build_prompt(PASSAGES, "What is Ohm's law?"),
        temperature=0.2,
    )
    warm = time.time() - t0
    cleaned, cites = grounding._valid_citations(answer, 1)
    from ethel.core.retrieval import grounding_report
    rep = grounding_report(cleaned, PASSAGES)
    r = CFG["retrieval"]
    verdict = lambda got, bar: "ok" if got >= bar else "FAILS"   # noqa: E731
    print(f"   answered in {warm:.1f}s")
    print(f"   cited a passage    : "
          f"{'yes' if cites else 'no - accepted anyway if well grounded'}")
    print(f"   names and numbers  : {rep['specific']:.0%} of {rep['specific_count']} held "
          f"(needs {r['min_specific_grounding']:.0%}) "
          f"{verdict(rep['specific'], r['min_specific_grounding'])}")
    if rep["unsupported"]:
        print(f"     not in passages  : {', '.join(rep['unsupported'])}")
    print(f"   overall overlap    : {rep['overall']:.0%} "
          f"(needs {r['min_answer_grounding']:.0%}) "
          f"{verdict(rep['overall'], r['min_answer_grounding'])}")
    print(f"   --- answer ---\n   {cleaned.strip()[:400].replace(chr(10), chr(10) + '   ')}")

    print("\n3. does it admit when the answer is not there?")
    t0 = time.time()
    refusal = backend.generate(
        grounding.SYSTEM,
        grounding.build_prompt(PASSAGES, "What did the court decide in Hyde v Wrench?"),
        temperature=0.2,
    )
    print(f"   answered in {time.time() - t0:.1f}s")
    honest = "INSUFFICIENT_CONTEXT" in refusal.upper()
    print(f"   said INSUFFICIENT_CONTEXT : {'yes' if honest else 'NO'}")
    if not honest:
        r2 = grounding_report(refusal, PASSAGES)
        caught = (r2["specific"] < CFG["retrieval"]["min_specific_grounding"]
                  or r2["overall"] < CFG["retrieval"]["min_answer_grounding"])
        print(f"   it answered anyway : names and numbers {r2['specific']:.0%}, "
              f"overall {r2['overall']:.0%}")
        if r2["unsupported"]:
            print(f"     invented         : {', '.join(r2['unsupported'])}")
        print("   " + ("gate 4 catches it and Ethel quotes the pack instead" if caught
                       else "GATE 4 DID NOT CATCH IT - this is a real hole"))
        print(f"   --- what it said ---\n   {refusal.strip()[:300].replace(chr(10), chr(10) + '   ')}")

    print("\n4. end to end through Ethel, with a real pack")
    sid = "modelcheck-tmp"
    try:
        if profile_mod.exists(sid):
            profile_mod.student_path(sid).unlink()
        profile_mod.create(sid, "Model Check", "0000")
        library.install(sid, ["cbu-ee220"])
        shelf = library.Shelf(sid)
        t0 = time.time()
        good = grounding.answer_question("What is Ohm's law?", shelf, backend, CFG)
        dt = time.time() - t0
        print(f"   in-scope question  : {good['mode']} in {dt:.1f}s")
        if good.get("fallback_reason"):
            print(f"     fell back because {good['fallback_reason']}")
        t0 = time.time()
        bad = grounding.answer_question("Who won the 1974 World Cup final?", shelf, backend, CFG)
        print(f"   out-of-scope       : {bad['mode']} in {time.time() - t0:.1f}s "
              "(refused before the model was ever called)")
    finally:
        if profile_mod.exists(sid):
            profile_mod.student_path(sid).unlink()
        library._remove_tree(config.INSTALLED_DIR / sid)

    print("\nverdict")
    if warm <= 15:
        print("  Fast enough to teach with.")
    elif warm <= 40:
        print("  Usable, but slow. Expect students to notice the pause.")
    else:
        print("  Too slow for a conversation on this machine. Either close other")
        print("  programs, or switch to a smaller model:  ollama pull gemma3:1b")
        print("  then set llm.model in data/config.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
