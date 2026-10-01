"""Paths and settings. Everything is relative to the install directory so the
whole tree can live on a USB stick and be copied onto any machine."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

CONTENT_DIR = ROOT / "content"
CATALOG_DIR = CONTENT_DIR / "catalog"
LIBRARY_DIR = CONTENT_DIR / "library"          # the offline "content hub"
DATA_DIR = ROOT / "data"
STUDENTS_DIR = DATA_DIR / "students"
INSTALLED_DIR = DATA_DIR / "installed"         # per-student installed packs
STATIC_DIR = Path(__file__).resolve().parent / "static"
LOCALES_DIR = Path(__file__).resolve().parent / "locales"

CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8770,
    "llm": {
        # "ollama" uses a local Ollama server on loopback. "none" runs the tutor
        # in extractive mode: it quotes the pack instead of rephrasing it.
        # Either way the tutor never invents content.
        "backend": "ollama",
        "endpoint": "http://127.0.0.1:11434",
        # A 1B-class model is the right default. The model is only ever a
        # phrasing layer over passages already retrieved from a course pack, so
        # it is held to the same four gates whatever its size - and a small one
        # answers fast enough that a student keeps asking.
        "model": "llama3.2:1b",
        # These four are chosen for the machine at start-up by ethel.hardware
        # unless set explicitly here. See the System check page for what was
        # picked and why.
        "autotune": True,
        "timeout_s": 150,
        "num_ctx": 4096,
        "num_thread": None,
        "max_tokens": 500,
    },
    "speech": {
        "enabled": True,
        # Two ways to point at Piper. `piper_exe` is a path to a self-contained
        # binary; `piper_cmd` is a full argv prefix, which is what you need when
        # Piper is the `piper-tts` Python package living in a virtualenv.
        # Voice .onnx files are looked for in models/piper/ by default.
        "piper_exe": "",
        "piper_cmd": [],
        "voices": {
            "en": {
                "female": "en_GB-jenny_dioco-medium.onnx",
                "male": "en_GB-alan-medium.onnx",
            },
            "bem": {"female": "", "male": ""},
            "loz": {"female": "", "male": ""},
        },
        "voices_dir": "",
    },
    "retrieval": {
        # How many passages go to the model. Auto-tuned down on weak hardware:
        # every extra passage is context the machine has to chew through.
        "top_k": 5,
        # A question must overlap the retrieved passage this much before the
        # tutor is willing to answer at all. Raising it makes the tutor more
        # willing to say "I don't know"; lowering it makes it chattier.
        "min_term_coverage": 0.34,
        "min_score": 0.5,
        # Gate 4a. Share of the answer's *specific* terms - names, years, codes,
        # long technical words - that must appear in the retrieved passages.
        # This is the strict one: it is what catches an invented case name.
        "min_specific_grounding": 0.7,
        # Gate 4b. Share of all content words. Deliberately generous - a
        # faithful paraphrase legitimately reaches for its own vocabulary, and
        # a high bar here discards correct answers.
        "min_answer_grounding": 0.25,
        # Gate 3. An answer that never cited a passage is accepted only if this
        # much of it demonstrably traces back to the material anyway.
        "min_uncited_grounding": 0.3,
    },
    "pedagogy": {
        "mastery_threshold": 0.8,
        "min_attempts_for_mastery": 3,
        "placement_items": 12,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_dirs() -> None:
    for p in (DATA_DIR, STUDENTS_DIR, INSTALLED_DIR, LIBRARY_DIR, CATALOG_DIR):
        p.mkdir(parents=True, exist_ok=True)


def load() -> dict[str, Any]:
    """Defaults, then hardware autotuning, then whatever the operator wrote.

    The order matters: an explicit value in `data/config.json` always wins, so
    autotuning can never override a deliberate choice.
    """
    ensure_dirs()
    user: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text("utf-8"))
        except (ValueError, OSError):
            user = {}

    cfg = json.loads(json.dumps(DEFAULTS))
    if user.get("llm", {}).get("autotune", DEFAULTS["llm"]["autotune"]):
        from . import hardware  # local import: keeps config importable anywhere

        cfg = _merge(cfg, {"llm": hardware.suggest_llm(),
                           "retrieval": hardware.suggest_retrieval()})
    cfg = _merge(cfg, user)

    env_port = os.environ.get("ETHEL_PORT")
    if env_port and env_port.isdigit():
        cfg["port"] = int(env_port)
    return cfg


def save(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), "utf-8")
