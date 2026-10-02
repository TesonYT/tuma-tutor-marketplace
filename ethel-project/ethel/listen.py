"""Speech in: letting a student talk to Ethel in their own language.

Where the voice side struggles, this side is comparatively well served. Piper
has no Bemba voice and nothing anywhere speaks Lozi - but **Zambezi Voice**
(huggingface.co/zambezivoice, Apache 2.0) publishes recognition models for
Bemba, Lozi, Tonga and Nyanja. A student who reads English but thinks in Bemba
can ask their question out loud.

The honest caveat, recorded per model in MODELS below: accuracy varies enormously
between these checkpoints. The Bemba XLS-R model reports WER 0.32, which is
usable. The Lozi Whisper one reports WER 83.1, which is not - roughly four words
in five come back wrong. Ethel shows the published figure next to the language
so nobody discovers that by watching a student give up.

Nothing here synthesises. These models listen only; see `speech.py` for output.

Design mirrors `speech.py` deliberately: a resident worker per model under the
voice virtualenv, capped by hardware tier, reaped when idle. These checkpoints
are 1.2 GB and up, so on a constrained machine exactly one may be loaded, and
loading it evicts a voice if memory is already committed.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import config, hardware, residency


class ListeningUnavailable(RuntimeError):
    pass


# Published figures from each model card, read 2026-08-28. WER is word error
# rate: lower is better, and anything above ~0.5 is generally too noisy to build
# a teaching interaction on.
MODELS: dict[str, dict[str, Any]] = {
    "en": {
        "repo": "Systran/faster-whisper-base.en",
        "language": "English",
        "wer": None,
        "size_mb": 141,
        "runtime": "faster-whisper",
        "verdict": ("Fast enough to hold a conversation with - about 3.6 seconds "
                    "for 4 seconds of speech on a 2-core machine. Needs no torch."),
    },
    "bem": {
        "repo": "zambezivoice/xls-r-300m-bem",
        "language": "Ichibemba",
        "wer": 0.32,
        "size_mb": 1203,
        "verdict": "Usable. About one word in three is wrong, so treat it as a "
                   "draft the student can correct, not as dictation.",
    },
    "loz": {
        "repo": "zambezivoice/xls-r-300m-loz",
        "language": "Silozi",
        "wer": None,
        "size_mb": 1203,
        "verdict": "Accuracy not published for this checkpoint. The group's Lozi "
                   "Whisper model reports WER 83.1, so test before relying on it.",
    },
    "nya": {
        "repo": "zambezivoice/xls-r-300m-nya",
        "language": "Chinyanja",
        "wer": None,
        "size_mb": 1203,
        "verdict": "Accuracy not published for this checkpoint.",
    },
    "toi": {
        "repo": "zambezivoice/xls-r-300m-toi",
        "language": "Chitonga",
        "wer": None,
        "size_mb": 1203,
        "verdict": "Accuracy not published for this checkpoint.",
    },
}


def models_dir(cfg: dict[str, Any]) -> Path:
    configured = (cfg.get("listen", {}) or {}).get("models_dir") or ""
    return Path(configured) if configured else config.ROOT / "models" / "asr"


def installed(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Recognition models physically present, same rule as voices and packs."""
    root = models_dir(cfg)
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (d / "config.json").exists():
            continue
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        known = MODELS.get(d.name, {})
        out.append({
            "language": d.name,
            "path": str(d),
            "language_name": known.get("language", d.name),
            "repo": known.get("repo"),
            "wer": known.get("wer"),
            "verdict": known.get("verdict"),
            "size_mb": round(size / 1024 / 1024, 1),
        })
    return out


def for_language(cfg: dict[str, Any], language: str) -> dict[str, Any] | None:
    return next((m for m in installed(cfg) if m["language"] == language), None)


def _worker_python(cfg: dict[str, Any]) -> str | None:
    from . import speech
    return speech._worker_python(cfg)


def _faster_whisper_ready(cfg: dict[str, Any]) -> bool:
    """faster-whisper is a far lighter dependency than torch, and separate."""
    python = _worker_python(cfg)
    if not python:
        return False
    global _fw_probe
    if _fw_probe is not None:
        return _fw_probe
    try:
        r = subprocess.run([python, "-c", "import faster_whisper"],
                           capture_output=True, timeout=300)
        _fw_probe = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        _fw_probe = False
    return _fw_probe


_fw_probe: bool | None = None


def available(cfg: dict[str, Any], language: str = "") -> bool:
    """Can this machine run recognition - for a language, or for anything?

    The two runtimes are independent: English uses faster-whisper, the Zambian
    languages use transformers. Reporting one blanket answer made a machine with
    a working English recogniser claim it could not hear at all.
    """
    from . import speech
    if not _worker_python(cfg):
        return False
    model = for_language(cfg, language) if language else None
    if model is not None:
        return (_faster_whisper_ready(cfg)
                if _is_ct2(model["path"]) else speech.mms_available(cfg))
    return _faster_whisper_ready(cfg) or speech.mms_available(cfg)


def _is_ct2(path: str) -> bool:
    return (Path(path) / "model.bin").exists()


def runtime_state(cfg: dict[str, Any]) -> str:
    """'yes' | 'no' | 'checking' | 'no venv' - for status pages that must not block."""
    from . import speech
    return speech.mms_probe_state(cfg)


# ---------------------------------------------------------------- the pool

class _Listener:
    def __init__(self, model: dict[str, Any], python: str, worker_py: Path) -> None:
        self.language = model["language"]
        self.lock = threading.Lock()
        self.last_used = time.time()
        self.requests = 0
        self.proc = subprocess.Popen(
            [python, str(worker_py), model["path"]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0,
        )
        info = json.loads(self._readline(600))
        if not info.get("ready"):
            self.kill()
            raise ListeningUnavailable(
                f"recognition model failed to load: {info.get('error')}")
        self.load_seconds = info.get("load_seconds")
        self.kind = info.get("kind")

    def _readline(self, timeout: float) -> bytes:
        guard = threading.Timer(timeout, self.kill)
        guard.start()
        try:
            line = self.proc.stdout.readline()
        finally:
            guard.cancel()
        if not line:
            err = b""
            try:
                err = self.proc.stderr.read() or b""
            except (OSError, ValueError):
                pass
            raise ListeningUnavailable(
                "recognition worker stopped responding"
                + (f": {err.decode('utf-8', 'replace')[:200]}" if err else ""))
        return line

    def alive(self) -> bool:
        return self.proc.poll() is None

    def transcribe(self, wav: bytes, timeout: float) -> dict[str, Any]:
        with self.lock:
            if not self.alive():
                raise ListeningUnavailable("recognition worker is not running")
            self.proc.stdin.write(json.dumps({"bytes": len(wav)}).encode() + b"\n")
            self.proc.stdin.write(wav)
            self.proc.stdin.flush()
            reply = json.loads(self._readline(timeout))
            if not reply.get("ok"):
                raise ListeningUnavailable(str(reply.get("error")))
            self.last_used = time.time()
            self.requests += 1
            residency.touch("listen", self.language)
            return reply

    def kill(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.kill()
        except (OSError, ValueError):
            pass


_pool: dict[str, _Listener] = {}
_lock = threading.Lock()
_reaper: threading.Thread | None = None


def _max_resident(cfg: dict[str, Any]) -> int:
    explicit = (cfg.get("listen", {}) or {}).get("max_resident")
    if explicit:
        return max(1, int(explicit))
    # These are far bigger than the voices, so a constrained machine holds one
    # and nothing else.
    return {"constrained": 1, "standard": 1, "roomy": 2}[hardware.profile()["tier"]]


def _idle_timeout(cfg: dict[str, Any]) -> float:
    return float((cfg.get("listen", {}) or {}).get("idle_s") or 240)


def _start_reaper(cfg: dict[str, Any]) -> None:
    global _reaper
    if _reaper is not None and _reaper.is_alive():
        return

    def loop() -> None:
        while True:
            time.sleep(30)
            cutoff = time.time() - _idle_timeout(cfg)
            with _lock:
                for lang, w in list(_pool.items()):
                    if w.last_used < cutoff or not w.alive():
                        w.kill()
                        residency.release("listen", lang)
                        del _pool[lang]

    _reaper = threading.Thread(target=loop, daemon=True, name="asr-reaper")
    _reaper.start()


def _acquire(cfg: dict[str, Any], model: dict[str, Any]) -> _Listener:
    python = _worker_python(cfg)
    if not python:
        raise ListeningUnavailable(
            "no interpreter with torch and transformers; run setup-voice.bat and "
            "install the MMS extras (see requirements-voice.txt)")
    worker_py = Path(__file__).parent / "asr_worker.py"
    with _lock:
        w = _pool.get(model["language"])
        if w is not None and w.alive():
            residency.touch("listen", model["language"])
            return w
        if w is not None:
            del _pool[model["language"]]
        while len(_pool) >= _max_resident(cfg):
            oldest = min(_pool.values(), key=lambda x: x.last_used)
            oldest.kill()
            residency.release("listen", oldest.language)
            del _pool[oldest.language]
    # These are the big ones. On a constrained machine this will evict every
    # resident voice, which is correct: a 1.2 GB recogniser and a voice do not
    # fit together, and swapping is worse than reloading.
    residency.make_room(model.get("size_mb") or 1200, "listen", model["language"])
    fresh = _Listener(model, python, worker_py)
    with _lock:
        _pool[model["language"]] = fresh
    residency.register("listen", model["language"],
                       model.get("size_mb") or 1200, fresh.kill)
    _start_reaper(cfg)
    return fresh


def pool_status() -> list[dict[str, Any]]:
    with _lock:
        return [{"language": w.language, "alive": w.alive(), "kind": w.kind,
                 "requests": w.requests, "load_seconds": w.load_seconds,
                 "idle_seconds": round(time.time() - w.last_used)}
                for w in _pool.values()]


def shutdown() -> None:
    with _lock:
        for w in _pool.values():
            w.kill()
            residency.release("listen", w.language)
        _pool.clear()


def transcribe(wav: bytes, cfg: dict[str, Any], language: str) -> dict[str, Any]:
    """WAV bytes -> what the student said, or a usable reason why not."""
    model = for_language(cfg, language)
    if model is None:
        known = MODELS.get(language)
        if known:
            raise ListeningUnavailable(
                f"No recognition model installed for {known['language']}. "
                f"Fetch it with:  python tools/get_voice.py --asr {language}  "
                f"({known['size_mb']} MB)")
        raise ListeningUnavailable(
            f"No recognition model exists for {language!r} that Ethel knows of.")
    timeout = float((cfg.get("listen", {}) or {}).get("timeout_s") or 300)
    reply = _acquire(cfg, model).transcribe(wav, timeout)
    return {
        "text": reply.get("text", ""),
        "language": language,
        "seconds": reply.get("seconds"),
        "audio_seconds": reply.get("audio_seconds"),
        "wer": model.get("wer"),
        "caution": model.get("verdict"),
    }


def status(cfg: dict[str, Any]) -> dict[str, Any]:
    have = installed(cfg)
    return {
        "runtime_ready": available(cfg),
        "runtime_state": runtime_state(cfg),
        "faster_whisper": _faster_whisper_ready(cfg),
        "ready_languages": [m["language"] for m in have if available(cfg, m["language"])],
        "models_dir": str(models_dir(cfg)),
        "installed": have,
        "max_resident": _max_resident(cfg),
        "idle_timeout_s": _idle_timeout(cfg),
        "resident": pool_status(),
        # `code` first and spread second would be overwritten: MODELS uses
        # "language" for the human-readable name, not the code.
        "known": [{**v, "code": k} for k, v in MODELS.items()],
        "note": ("Zambezi Voice models listen; they do not speak. Recognition "
                 "quality varies a lot between languages - the published word "
                 "error rate is shown so a bad one is obvious before a student "
                 "relies on it."),
    }
