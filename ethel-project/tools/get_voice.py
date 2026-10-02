"""Fetch a voice onto this machine. Run once, on a machine with internet.

    python tools/get_voice.py --list
    python tools/get_voice.py bem                 # Meta MMS Bemba
    python tools/get_voice.py en_GB-alan-medium   # a Piper English voice

This is the one script in the project that is *supposed* to touch the network,
so it deliberately does not engage the airlock. Everything it downloads lands in
`models/` and is then used entirely offline. To move a voice to a machine that
has no internet, run this somewhere connected and copy the `models/` folder.

On Lozi: there is nothing to download. No Lozi text-to-speech model exists - not
in Piper, not in Meta's MMS, and none on the Hugging Face Hub as of 2026-08-27.
Asking for it prints that rather than failing with a 404, because "does not
exist" and "you typed it wrong" are different problems for whoever is setting a
classroom up. The route for Lozi is a person recording the lessons; see the
`translations.<lang>.lessons.<id>.audio` fields in the pack format.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HF = "https://huggingface.co"

# MMS ships one checkpoint per language. These are the files a VitsModel needs.
MMS_FILES = ["config.json", "model.safetensors", "tokenizer_config.json",
             "vocab.json", "special_tokens_map.json"]

# Zambezi Voice recognition models - speech IN, not out. Apache 2.0.
ASR_FILES = ["config.json", "preprocessor_config.json", "tokenizer_config.json",
             "vocab.json", "special_tokens_map.json", "added_tokens.json",
             "alphabet.json", "pytorch_model.bin"]
ASR_OPTIONAL = {"special_tokens_map.json", "added_tokens.json", "alphabet.json",
                "tokenizer_config.json", "vocab.json"}

MMS_LANGUAGES = {
    "bem": ("facebook/mms-tts-bem", "Ichibemba (Bemba)"),
    "nya": ("facebook/mms-tts-nya", "Chinyanja (Chewa)"),
    "sna": ("facebook/mms-tts-sna", "chiShona"),
    "swh": ("facebook/mms-tts-swh", "Kiswahili"),
}

# Languages people will ask for that genuinely have no model anywhere.
NO_MODEL = {
    "loz": "Silozi (Lozi)",
    "toi": "Chitonga (Tonga)",
    "lue": "Luvale",
    "lun": "Lunda",
    "kqn": "Kaonde",
}

PIPER_BASE = f"{HF}/rhasspy/piper-voices/resolve/main"


def _download(url: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "ethel-get-voice"})
    # Carriage-return progress is fine on a terminal and turns into tens of
    # thousands of lines when the output is piped or logged. Only animate when
    # someone is actually watching.
    live = sys.stdout.isatty()
    with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
        total = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
            if live:
                print(f"\r  {dest.name}: {total/1024/1024:6.1f} MB", end="", flush=True)
    print(f"\r  {dest.name}: {total/1024/1024:6.1f} MB")
    return total


def get_mms(code: str) -> int:
    repo, name = MMS_LANGUAGES[code]
    target = ROOT / "models" / "mms" / code
    print(f"{name} - {repo}")
    if (target / "config.json").exists():
        print(f"  already present at {target}")
        return 0
    got = 0
    for fn in MMS_FILES:
        url = f"{HF}/{repo}/resolve/main/{fn}"
        try:
            got += _download(url, target / fn)
        except urllib.error.HTTPError as exc:
            if fn == "special_tokens_map.json" and exc.code == 404:
                continue                      # optional for some checkpoints
            print(f"  FAILED {fn}: HTTP {exc.code}")
            return 1
    print(f"  {got/1024/1024:.1f} MB into {target}")
    print()
    print("  This voice needs torch + transformers in the voice venv:")
    print("     .venv\\Scripts\\python.exe -m pip install torch transformers")
    print("  Roughly 250 MB more. Until then the System check page will say the")
    print("  model is present but the runtime is missing.")
    return 0


def get_asr(code: str) -> int:
    """Fetch a Zambezi Voice recognition model so a student can speak."""
    sys.path.insert(0, str(ROOT))
    from ethel.listen import MODELS

    if code not in MODELS:
        print(f"No recognition model known for {code!r}. Known: "
              + ", ".join(MODELS))
        return 1
    info = MODELS[code]
    target = ROOT / "models" / "asr" / code
    print(f"{info['language']} recognition - {info['repo']}")
    print(f"  about {info['size_mb']} MB. WER "
          + (f"{info['wer']}" if info["wer"] is not None else "not published")
          + f". {info['verdict']}")
    if (target / "config.json").exists():
        print(f"  already present at {target}")
        return 0
    got = 0
    for fn in ASR_FILES:
        url = f"{HF}/{info['repo']}/resolve/main/{fn}"
        try:
            got += _download(url, target / fn)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and fn in ASR_OPTIONAL:
                continue
            print(f"  FAILED {fn}: HTTP {exc.code}")
            return 1
    # The 5-gram language model, when the checkpoint ships one, is a large
    # accuracy win on these languages - worth the extra files.
    for fn in ("language_model/5gram.bin", "language_model/attrs.json",
               "language_model/unigrams.txt"):
        try:
            got += _download(f"{HF}/{info['repo']}/resolve/main/{fn}", target / fn)
        except urllib.error.HTTPError:
            pass
    print(f"  {got/1024/1024:.1f} MB into {target}")
    print("  Needs torch + transformers in the voice venv (see requirements-voice.txt).")
    return 0


def get_piper(voice_id: str) -> int:
    """Piper voices are laid out by language, e.g. en/en_GB/alan/medium/."""
    parts = voice_id.split("-")
    if len(parts) != 3:
        print(f"'{voice_id}' is not a Piper voice id (expected like en_GB-alan-medium)")
        return 1
    locale, speaker, quality = parts
    family = locale.split("_")[0]
    target = ROOT / "models" / "piper"
    base = f"{PIPER_BASE}/{family}/{locale}/{speaker}/{quality}/{voice_id}.onnx"
    if (target / f"{voice_id}.onnx").exists():
        print(f"  {voice_id} already present")
        return 0
    try:
        _download(base, target / f"{voice_id}.onnx")
        _download(base + ".json", target / f"{voice_id}.onnx.json")
    except urllib.error.HTTPError as exc:
        print(f"  FAILED: HTTP {exc.code} - check the voice id at "
              f"{HF}/rhasspy/piper-voices")
        return 1
    print(f"  installed into {target}")
    return 0


def cmd_list() -> int:
    print("Piper (English) - browse the full set at "
          f"{HF}/rhasspy/piper-voices\n")
    for v in ("en_GB-alan-medium", "en_GB-jenny_dioco-medium",
              "en_GB-alba-medium", "en_US-ryan-medium", "en_US-amy-medium"):
        here = (ROOT / "models" / "piper" / f"{v}.onnx").exists()
        print(f"  {'installed' if here else '         '}  {v}")

    print("\nMeta MMS - the only route to some African languages\n")
    for code, (repo, name) in MMS_LANGUAGES.items():
        here = (ROOT / "models" / "mms" / code / "config.json").exists()
        print(f"  {'installed' if here else '         '}  {code:5} {name:22} {repo}")

    print("\nSpeech IN - Zambezi Voice recognition (these listen, they cannot speak)\n")
    from ethel.listen import MODELS as ASR
    for code, info in ASR.items():
        here = (ROOT / "models" / "asr" / code / "config.json").exists()
        wer = f"WER {info['wer']}" if info["wer"] is not None else "WER unpublished"
        print(f"  {'installed' if here else '         '}  {code:5} "
              f"{info['language']:22} {info['size_mb']:5} MB  {wer}")
    print("     fetch with:  python tools/get_voice.py --asr bem")

    print("\nNo TEXT-TO-SPEECH model exists anywhere for these. Record a speaker:\n")
    for code, name in NO_MODEL.items():
        print(f"  {'-':>9}  {code:5} {name}")
    print("\n  Packs carry recorded audio per segment; see the pack format's")
    print("  translations.<lang>.lessons.<id>.audio fields. Ethel prefers a")
    print("  recording over synthesis in every language, so this is not a")
    print("  second-class path - it is the better one.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="get_voice")
    ap.add_argument("voice", nargs="?", help="a language code (bem) or Piper voice id")
    ap.add_argument("--list", action="store_true", help="what is available and installed")
    ap.add_argument("--asr", metavar="LANG",
                    help="fetch a Zambezi Voice recognition model (bem, loz, nya, toi)")
    args = ap.parse_args()

    if args.asr:
        return get_asr(args.asr)
    if args.list or not args.voice:
        return cmd_list()

    v = args.voice
    if v in NO_MODEL:
        print(f"There is no text-to-speech model for {NO_MODEL[v]} ({v}).")
        print("Not in Piper, not in Meta's MMS, and none on the Hugging Face Hub")
        print("as of 2026-08-27. This is not something you have failed to install.")
        print()
        print("The route that works: record a speaker reading each lesson segment,")
        print("and put the files in the pack under")
        print("  translations." + v + ".lessons.<lesson>.audio")
        print("Ethel plays a recording in preference to any synthetic voice.")
        return 2
    if v in MMS_LANGUAGES:
        return get_mms(v)
    return get_piper(v)


if __name__ == "__main__":
    sys.exit(main())
