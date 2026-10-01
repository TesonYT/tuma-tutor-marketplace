"""Is speech input working, and how well does it hear?

    python tools/listencheck.py bem            # round-trip through the TTS voice
    python tools/listencheck.py bem --wav a.wav "what was actually said"

Mirrors `modelcheck.py` for the recognition side. Answers, in order:

  1. Is the runtime present (torch + transformers in the voice venv)?
  2. Is a recognition model installed for this language?
  3. Does it transcribe, and how fast?
  4. How close is the transcript to what was actually said?

**On the round-trip test.** With no recording to hand, this speaks a phrase with
the MMS voice and asks the recognition model to hear it back. That genuinely
exercises the whole path - encoder, resampling, worker protocol, decoding - and
it is a *far easier* test than real speech: one synthetic speaker, no room, no
microphone, no accent, no background noise. Treat a good round-trip score as
"the plumbing works", never as "this will understand your students". The number
that matters for that is the model card's WER against real recordings, which is
0.32 for Bemba and unpublished for the rest.

Pass `--wav` with a real recording and the true text to get an honest figure.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ethel import airlock  # noqa: E402

airlock.engage()

from ethel import config, listen, speech  # noqa: E402

CFG = config.load()


def _wer(reference: str, hypothesis: str) -> float:
    """Word error rate: edit distance over words, normalised by reference length."""
    r = reference.lower().split()
    h = hypothesis.lower().split()
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw)))
        prev = cur
    return prev[-1] / len(r)


PHRASES = {
    "bem": "Mwapoleni mukwai natotela",
    "nya": "Muli bwanji zikomo kwambiri",
}


def main() -> int:
    ap = argparse.ArgumentParser(prog="listencheck")
    ap.add_argument("language", help="bem, loz, nya, toi")
    ap.add_argument("--wav", help="a real recording to transcribe instead")
    ap.add_argument("said", nargs="?", default="", help="what the recording says")
    args = ap.parse_args()
    lang = args.language

    print(f"Ethel - speech input check [{lang}]\n")

    known = listen.MODELS.get(lang)
    if known:
        wer = known["wer"]
        print(f"model     : {known['repo']}")
        print(f"published : WER {wer if wer is not None else 'not published'} "
              f"- {known['verdict']}")
    print(f"runtime   : {'ready' if listen.available(CFG) else 'NOT READY'}")
    if not listen.available(CFG):
        print("\nFAIL  torch + transformers are not in the voice venv.")
        print("      See requirements-voice.txt.")
        return 1

    have = listen.for_language(CFG, lang)
    if not have:
        print(f"\nFAIL  no model installed for {lang!r}.")
        print(f"      python tools/get_voice.py --asr {lang}")
        return 1
    print(f"installed : {have['size_mb']} MB at {have['path']}")

    if args.wav:
        wav = Path(args.wav).read_bytes()
        truth = args.said
        source = f"recording {args.wav}"
    else:
        phrase = PHRASES.get(lang)
        if not phrase:
            print(f"\nNo test phrase for {lang!r}. Pass --wav with a real recording.")
            return 2
        voice = speech.resolve_voice(CFG, None, lang, "female")
        if not voice:
            print(f"\nNo {lang} voice installed to generate test audio with.")
            print(f"      python tools/get_voice.py {lang}")
            print("      Or pass --wav with a real recording.")
            return 2
        print(f"\nspeaking test phrase with {voice['id']} ...")
        t0 = time.time()
        wav = speech.speak(phrase, CFG, language=lang)
        print(f"  {time.time() - t0:.1f}s")
        truth = phrase
        source = "synthetic speech (an easy test - see this file's docstring)"

    with wave.open(io.BytesIO(wav)) as w:
        print(f"audio     : {w.getnframes() / w.getframerate():.1f}s, "
              f"{w.getframerate()} Hz, {w.getnchannels()} channel(s)")

    print(f"\nlistening ({source}) ...")
    t0 = time.time()
    out = listen.transcribe(wav, CFG, lang)
    cold = time.time() - t0
    print(f"  cold : {cold:.1f}s")
    t0 = time.time()
    out2 = listen.transcribe(wav, CFG, lang)
    warm = time.time() - t0
    print(f"  warm : {warm:.1f}s for {out2.get('audio_seconds')}s of audio")

    print(f"\n  said  : {truth}")
    print(f"  heard : {out['text'] or '(nothing)'}")
    if truth:
        score = _wer(truth, out["text"])
        print(f"  WER   : {score:.2f} on this sample")
        if not args.wav:
            print("          Synthetic speech only. Real students will score worse.")

    print(f"\nresident: {listen.pool_status()}")
    listen.shutdown()

    print("\nverdict")
    if warm <= 3:
        print("  Fast enough to speak to.")
    elif warm <= 10:
        print("  Usable, but the student waits. Expect them to notice.")
    else:
        print("  Too slow to hold a conversation with on this machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
