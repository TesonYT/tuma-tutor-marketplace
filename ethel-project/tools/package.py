"""Build a distributable Ethel.

    python tools/package.py                  # core + course packs
    python tools/package.py --with-voices    # also embed the voice files
    python tools/package.py --out D:\\usb

The point of this script is to make the real shape of the project visible.
**Ethel is about a megabyte.** Everything that makes the install look enormous -
the language model, the voices, the recognisers, the Python packages they need -
is an optional add-on that belongs to the machine, not to the courseware.

So the default bundle carries the tutor and her course packs and nothing else,
plus a `REQUIREMENTS.json` naming exactly which models a target machine needs and
where to get them. A site with ten machines copies one small folder ten times and
fetches the heavy parts once.

Two safety rules, both learned the hard way elsewhere:

* **Allowlist, never denylist.** Only paths that match an explicit rule go in.
  A denylist ships whatever you forgot to think about - and this tree contains
  student records, PINs and a config file with local paths in it.
* **Refuse to build if a student record or a secret could leak.** The scan runs
  over the staged files, not the source tree, so it sees exactly what would ship.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Every file that ships must match one of these. Anything else is left behind.
ALLOW = [
    "README.md",
    "ARCHITECTURE.md",
    "run.bat",
    "setup-voice.bat",
    "requirements-voice.txt",
    "serve.py",
    ".gitignore",
    "ethel/*.py",
    "ethel/core/*.py",
    "ethel/static/*",
    "ethel/locales/*.json",
    "content/catalog/*.json",
    "content/library/*/pack.json",
    "content/library/*/glossary-*.json",
    "content/library/*/audio/*",          # human recordings, when a pack has them
    "tools/*.py",
]

# Voice and model files, included only with --with-voices.
VOICE_ALLOW = [
    "models/piper/*.onnx",
    "models/piper/*.onnx.json",
]

# Never ship, even if some future allowlist rule would match them.
NEVER = [
    "data/*",            # student records, PINs, installed packs
    ".venv/*",
    "**/__pycache__/*",
    "*.pyc",
    ".claude/*",         # local editor/session config
]

# A staged file containing any of these is a build-stopping problem.
LEAK_PATTERNS = [
    # A *value*, not the identifier. The source code legitimately names these
    # fields; a shipped student record would carry the hex digest beside them.
    (re.compile(r'"(?:pin_hash|pin_salt)"\s*:\s*"[0-9a-fA-F]{8,}"'),
     "a student PIN hash or salt"),
    (re.compile(r"C:\\\\Users\\\\(?!TESON\\\\Desktop\\\\Ethel)", re.I), "an absolute user path"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "an email address"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "a MAC address"),
]
# Files where a path or address is legitimate documentation, not a leak.
LEAK_EXEMPT = {"README.md", "ARCHITECTURE.md", "requirements-voice.txt",
               "setup-voice.bat"}


def matches(rel: str, patterns: list[str]) -> bool:
    rel = rel.replace("\\", "/")
    return any(fnmatch.fnmatch(rel, p) for p in patterns)


def collect(with_voices: bool) -> list[Path]:
    allow = ALLOW + (VOICE_ALLOW if with_voices else [])
    out: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if matches(rel, NEVER):
            continue
        if matches(rel, allow):
            out.append(path)
    return out


def scan(files: list[Path]) -> list[str]:
    problems: list[str] = []
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if rel in LEAK_EXEMPT:
            continue
        try:
            text = f.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue                      # binary, nothing to leak in text form
        for pattern, what in LEAK_PATTERNS:
            m = pattern.search(text)
            if m:
                problems.append(f"{rel}: {what} ({m.group(0)[:40]!r})")
    return problems


def requirements(with_voices: bool) -> dict:
    """What the target machine still has to fetch, and how."""
    from ethel import listen, speech

    return {
        "_note": (
            "Ethel runs with none of this. The tutor teaches from her course "
            "packs using retrieval alone; every item below only changes how "
            "things are said or heard, never what is true."
        ),
        "python": "3.10 or newer. Nothing else is required for the tutor itself.",
        "optional": {
            "language_model": {
                "what": "Rephrases passages already retrieved from a pack.",
                "without_it": "Extractive mode - Ethel quotes the pack verbatim.",
                "install": ["winget install --id Ollama.Ollama -e",
                            "ollama pull llama3.2:1b"],
                "size_mb": 1300,
                "tip": ("Ollama ships CUDA and ROCm runtimes in lib/ollama/. On a "
                        "machine with no NVIDIA or AMD GPU those folders are dead "
                        "weight and can be deleted - about 2.6 GB."),
            },
            "voices": {
                "what": "Reads lessons aloud.",
                "without_it": "The read-aloud button is disabled and says why.",
                "install": ["setup-voice.bat",
                            "python tools/get_voice.py en_GB-alan-medium",
                            "python tools/get_voice.py en_GB-jenny_dioco-medium"],
                "size_mb": 121 if not with_voices else 0,
                "included_in_this_bundle": with_voices,
                "languages": {
                    code: {k: v for k, v in info.items() if k != "note"}
                    for code, info in speech.LANGUAGE_TTS_REALITY.items()
                },
            },
            "speech_input": {
                "what": "Lets a student speak instead of typing.",
                "without_it": "The microphone button is hidden.",
                "install": ["setup-voice.bat",
                            "pip install torch --index-url https://download.pytorch.org/whl/cpu",
                            "pip install transformers",
                            "python tools/get_voice.py --asr bem"],
                "size_mb": 1214,
                "models": {c: {"repo": m["repo"], "wer": m["wer"],
                               "size_mb": m["size_mb"]}
                           for c, m in listen.MODELS.items()},
            },
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(prog="package")
    ap.add_argument("--out", default="", help="where to write the bundle")
    ap.add_argument("--with-voices", action="store_true",
                    help="embed the Piper voice files (adds ~121 MB)")
    ap.add_argument("--force", action="store_true",
                    help="build even if the leak scan complains")
    args = ap.parse_args()

    files = collect(args.with_voices)
    if not files:
        print("Nothing matched the allowlist. Refusing to build an empty bundle.")
        return 1

    print(f"staging {len(files)} files")
    problems = scan(files)
    if problems:
        print(f"\nREFUSING TO BUILD - {len(problems)} thing(s) that must not ship:")
        for p in problems[:20]:
            print(f"  {p}")
        if not args.force:
            print("\nFix these, or re-run with --force if you are certain.")
            return 1
        print("\n--force given; building anyway.")

    from ethel import __version__

    out_dir = Path(args.out) if args.out else ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"Ethel-{__version__}" + ("-with-voices" if args.with_voices else "")
    zip_path = out_dir / f"{name}.zip"

    reqs = requirements(args.with_voices)
    total = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            rel = f.relative_to(ROOT).as_posix()
            z.write(f, f"{name}/{rel}")
            total += f.stat().st_size
        z.writestr(f"{name}/REQUIREMENTS.json",
                   json.dumps(reqs, indent=2, ensure_ascii=False))
        # A digest of every shipped file, so a site can verify a USB copy.
        manifest = {
            f.relative_to(ROOT).as_posix():
                hashlib.sha256(f.read_bytes()).hexdigest()[:16]
            for f in files
        }
        z.writestr(f"{name}/MANIFEST.json", json.dumps(manifest, indent=2))

    size = zip_path.stat().st_size
    print(f"\nwrote {zip_path}")
    print(f"  {len(files)} files, {total/1024/1024:.1f} MB raw, "
          f"{size/1024/1024:.1f} MB compressed")
    if not args.with_voices:
        print("\n  This bundle is the tutor and her courses. It teaches on its own.")
        print("  REQUIREMENTS.json lists the optional models and what each one buys.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
