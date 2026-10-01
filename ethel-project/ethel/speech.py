"""Voice, offline - and held open.

Text to speech runs through Piper. The voice files are ordinary `.onnx` files
sitting in `models/piper/`; nothing streams to a cloud service.

**The important design point is that voices stay loaded.** The obvious
implementation shells out to Piper once per sentence, which reloads a 63 MB
model from disk every time - measured at 17-20 seconds for one short sentence on
a 2-core machine, with no warm-up benefit at all. Instead `ethel/voice_worker.py`
is spawned once per voice under the voice virtualenv and kept alive, so the model
is loaded once and later requests are pure synthesis.

Keeping models resident costs memory, which on a 4 GB machine is the scarce
thing. So the pool size follows the hardware tier - one resident voice on a
constrained machine, more on a bigger one - and idle voices are evicted after a
few minutes to give the RAM back. Switching voices on a constrained machine
therefore costs one reload; that is the honest trade and the System check page
shows which voices are currently resident.

If the worker cannot be used - no virtualenv, or only a standalone Piper binary
is installed - everything still works through the original one-shot subprocess
path. It is just slow, and `status()` says which mode is in use.

On Bemba and Lozi, the situation is not symmetric and the code says so rather
than pretending otherwise (see LANGUAGE_TTS_REALITY):

* **Bemba** has no Piper voice, but Meta's MMS covers it. `tools/get_voice.py
  bem` fetches `facebook/mms-tts-bem` into `models/mms/bem/`, and the worker
  runs it - that path needs torch and transformers in the voice venv, which is
  why it is opt-in rather than a dependency of the whole app.
* **Lozi has no text-to-speech model at all.** Not in Piper, not in MMS, and
  none on the Hugging Face Hub as of 2026-08-27. The only honest route is a
  human recording, so packs can carry recorded audio per segment and Ethel
  prefers a recording over synthesis wherever one exists.

What Ethel will not do in either language is read the text with an English
voice. That produces confident-sounding nonsense, which is the failure mode the
whole design exists to avoid.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import config, hardware, residency


class VoiceUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- speaking modes

# Pace is `length_scale`: above 1.0 stretches phonemes, so the voice slows down
# without changing pitch. This matters more here than it would in a consumer
# product - many students are working in their second or third language, and a
# slower reading of a definition is a real accommodation, not a gimmick.
PACES: dict[str, dict[str, Any]] = {
    "brisk":    {"label": "Brisk",    "length_scale": 0.9,
                 "hint": "For revision, when you already know the material."},
    "normal":   {"label": "Normal",   "length_scale": 1.0,
                 "hint": "The voice's natural pace."},
    "measured": {"label": "Measured", "length_scale": 1.2,
                 "hint": "A little slower. Good for a first pass through new ideas."},
    "slow":     {"label": "Slow",     "length_scale": 1.45,
                 "hint": "Clearly slower, for difficult passages or a second language."},
}
DEFAULT_PACE = "normal"

# Piper's voice metadata does not record speaker gender, so it cannot be derived
# from the file. This table covers the voices Ethel ships plus common English
# ones; anything unknown is reported as unknown rather than guessed, and
# `speech.voice_gender` in data/config.json can override or extend it.
_KNOWN_GENDER: dict[str, str] = {
    "en_GB-jenny_dioco-medium": "female",
    "en_GB-alba-medium": "female",
    "en_GB-cori-high": "female",
    "en_GB-cori-medium": "female",
    "en_GB-southern_english_female-low": "female",
    "en_GB-alan-medium": "male",
    "en_GB-alan-low": "male",
    "en_GB-northern_english_male-medium": "male",
    "en_US-amy-medium": "female",
    "en_US-lessac-medium": "female",
    "en_US-kathleen-low": "female",
    "en_US-ryan-medium": "male",
    "en_US-joe-medium": "male",
    "en_US-danny-low": "male",
}


def voices_dir(cfg: dict[str, Any]) -> Path:
    configured = (cfg.get("speech", {}) or {}).get("voices_dir") or ""
    return Path(configured) if configured else config.ROOT / "models" / "piper"


# What is known to exist, so the System check page can distinguish "you have not
# installed it" from "it does not exist anywhere". Checked against the MMS
# release and a Hub search on 2026-08-27, and Zambezi Voice on 2026-08-28.
#
# On Zambezi Voice (huggingface.co/zambezivoice): a real Zambian language
# project covering Bemba, Lozi, Tonga and Nyanja, Apache 2.0. Every one of its
# twenty models is `automatic-speech-recognition` - Whisper and wav2vec2/XLS-R
# fine-tunes. It is the best available answer for a student *speaking* these
# languages, and no answer at all for reading a lesson aloud, because it does
# not synthesise. Recorded in `asr` below so nobody has to rediscover this.
LANGUAGE_TTS_REALITY: dict[str, dict[str, Any]] = {
    "en": {"piper": True, "mms": True, "asr": "whisper (not wired up here)",
           "note": "Several Piper voices, male and female."},
    "bem": {"piper": False, "mms": True, "mms_repo": "facebook/mms-tts-bem",
            "asr": "zambezivoice/xls-r-300m-bem (WER 0.32, Apache 2.0)",
            "note": "No Piper voice. Meta's MMS covers Bemba - install it with "
                    "tools/get_voice.py bem, or record a person instead."},
    "loz": {"piper": False, "mms": False,
            "asr": "zambezivoice/whisper-medium-loz (WER 83.1 - poor) and "
                   "zambezivoice/xls-r-300m-loz, Apache 2.0. Recognition only; "
                   "neither can speak.",
            "note": "No text-to-speech model for Lozi exists - not in Piper, not "
                    "in Meta's MMS, and none on the Hugging Face Hub as of "
                    "2026-08-27. Lozi audio must be recorded by a speaker; packs "
                    "can carry those recordings and Ethel prefers them over "
                    "synthesis."},
}


def mms_dir(cfg: dict[str, Any]) -> Path:
    configured = (cfg.get("speech", {}) or {}).get("mms_dir") or ""
    return Path(configured) if configured else config.ROOT / "models" / "mms"


_mms_probe: dict[str, bool] = {}
_mms_probing: set[str] = set()
_probe_lock = threading.Lock()


def _run_mms_probe(python: str) -> None:
    try:
        r = subprocess.run([python, "-c", "import torch, transformers"],
                           capture_output=True, timeout=900)
        ok = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    with _probe_lock:
        _mms_probe[python] = ok
        _mms_probing.discard(python)


def mms_probe_state(cfg: dict[str, Any]) -> str:
    """'yes' | 'no' | 'checking' | 'no venv'.

    Answering this properly means starting a Python process and importing
    torch. On a machine short of memory that measured **5 minutes 9 seconds** -
    torch is most of a gigabyte of DLLs being paged in from disk. Blocking the
    System check page on it would make the page look broken, and guessing would
    make it lie, so the probe runs in the background and the page reports
    "checking" until it finishes. The answer is then cached: it only changes
    when somebody installs or removes packages.
    """
    python = _worker_python(cfg)
    if not python:
        return "no venv"
    with _probe_lock:
        if python in _mms_probe:
            return "yes" if _mms_probe[python] else "no"
        if python not in _mms_probing:
            _mms_probing.add(python)
            threading.Thread(target=_run_mms_probe, args=(python,),
                             daemon=True, name="mms-probe").start()
    return "checking"


def mms_available(cfg: dict[str, Any], wait: bool = False) -> bool:
    """True only when the runtime is known to work. 'checking' counts as no.

    Callers that must have a definite answer - a synthesis request, not a status
    page - pass wait=True and accept the delay.
    """
    state = mms_probe_state(cfg)
    if state == "checking" and wait:
        python = _worker_python(cfg) or ""
        _run_mms_probe(python)
        return _mms_probe.get(python, False)
    return state == "yes"


def catalogue(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Every voice file actually present, with what its metadata says about it.

    Ethel offers what is on the machine, not a menu of things she could download
    - the same rule the course packs follow.
    """
    overrides = (cfg.get("speech", {}) or {}).get("voice_gender") or {}
    out: list[dict[str, Any]] = []
    root = voices_dir(cfg)
    for onnx in sorted(root.glob("*.onnx") if root.exists() else []):
        vid = onnx.stem
        meta: dict[str, Any] = {}
        side = onnx.with_suffix(".onnx.json")
        if side.exists():
            try:
                meta = json.loads(side.read_text("utf-8"))
            except (ValueError, OSError):
                meta = {}
        lang = meta.get("language") or {}
        speaker = str(meta.get("dataset") or vid.split("-")[1] if "-" in vid else vid)
        out.append({
            "id": vid,
            "path": str(onnx),
            "label": speaker.replace("_", " ").title(),
            "gender": overrides.get(vid) or _KNOWN_GENDER.get(vid, "unknown"),
            "language_code": lang.get("code") or "",
            "language_family": lang.get("family") or "",
            "language_name": lang.get("name_english") or "",
            "region": lang.get("country_english") or "",
            "quality": (meta.get("audio") or {}).get("quality") or "",
            "sample_rate": (meta.get("audio") or {}).get("sample_rate"),
            "size_mb": round(onnx.stat().st_size / 1024 / 1024, 1),
            "engine": "piper",
        })

    # MMS voices: one directory per language, as downloaded by tools/get_voice.py.
    # This is the only route to Bemba, and there is no route at all to Lozi.
    root = mms_dir(cfg)
    if root.exists():
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            if not (d / "config.json").exists():
                continue
            code = d.name
            size = sum(f.stat().st_size for f in d.glob("*") if f.is_file())
            out.append({
                "id": f"mms-{code}",
                "path": str(d),
                "label": f"MMS {code}",
                "gender": overrides.get(f"mms-{code}", "unknown"),
                "language_code": code,
                "language_family": code,
                "language_name": {"bem": "Ichibemba", "loz": "Silozi",
                                  "nya": "Chinyanja"}.get(code, code),
                "region": "",
                "quality": "mms",
                "sample_rate": None,
                "size_mb": round(size / 1024 / 1024, 1),
                "engine": "mms",
            })
    return out


def resolve_voice(cfg: dict[str, Any], voice_id: str | None = None,
                  language: str = "en", gender: str = "female") -> dict[str, Any] | None:
    """Pick a voice: an explicit id if it exists, else language + gender."""
    cat = catalogue(cfg)
    if not cat:
        return None
    if voice_id:
        for v in cat:
            if v["id"] == voice_id:
                return v
        # An id that is no longer installed falls through to the default rather
        # than failing: a student's saved choice must not brick their audio.
    family = {"en": "en"}.get(language, language)
    in_language = [v for v in cat if v["language_family"] == family]
    if not in_language:
        return None                       # never read one language in another
    by_gender = [v for v in in_language if v["gender"] == gender]
    return (by_gender or in_language)[0]


# ---------------------------------------------------------------- engine lookup

def _candidates(cfg: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Every way we know of to invoke Piper, in priority order.

    Piper exists in two shapes in the wild: the original self-contained C++
    binary, and the current `piper-tts` Python package whose console script
    lives inside a virtualenv. Both accept `--model` and `--output_file`, so
    Ethel only needs to find one of them - but a venv console script is not on
    PATH, which is why `speech.piper_cmd` exists as an explicit escape hatch.

    Returned as (label, argv-prefix) pairs so the System check page can show
    what was tried rather than just reporting failure.
    """
    sp = cfg.get("speech", {}) or {}
    out: list[tuple[str, list[str]]] = []

    cmd = sp.get("piper_cmd")
    if cmd:
        out.append(("speech.piper_cmd", [str(x) for x in cmd]))
    exe = sp.get("piper_exe")
    if exe:
        out.append(("speech.piper_exe", [str(exe)]))
    # Ethel's own voice venv, created by setup-voice.bat. Found without any
    # configuration, which is the point: voice should be one script away, not a
    # path someone has to look up.
    out.append(("own venv", [str(config.ROOT / ".venv" / "Scripts" / "piper.exe")]))
    out.append(("own venv", [str(config.ROOT / ".venv" / "bin" / "piper")]))
    out.append(("bundled", [str(config.ROOT / "piper" / "piper.exe")]))
    out.append(("bundled", [str(config.ROOT / "piper" / "piper")]))
    found = shutil.which("piper")
    out.append(("PATH", [found] if found else ["piper (not on PATH)"]))
    return out


def _runnable(argv: list[str]) -> bool:
    head = argv[0]
    if not head or head.endswith("(not on PATH)"):
        return False
    return Path(head).exists() or shutil.which(head) is not None


def _piper_cmd(cfg: dict[str, Any]) -> list[str] | None:
    for _label, argv in _candidates(cfg):
        if _runnable(argv):
            return argv
    return None


def _worker_python(cfg: dict[str, Any]) -> str | None:
    """An interpreter that can `import piper`, for the persistent worker.

    Ethel's own venv is the expected answer. An explicitly configured one wins,
    for anyone who already has piper-tts somewhere else.
    """
    explicit = (cfg.get("speech", {}) or {}).get("worker_python") or ""
    if explicit and Path(explicit).exists():
        return explicit
    for c in (config.ROOT / ".venv" / "Scripts" / "python.exe",
              config.ROOT / ".venv" / "bin" / "python"):
        if c.exists():
            return str(c)
    return None


# ---------------------------------------------------------------- the pool

class _Worker:
    """One resident voice: a process with the model already in memory."""

    def __init__(self, voice: dict[str, Any], python: str, worker_py: Path) -> None:
        self.voice_id = voice["id"]
        self.lock = threading.Lock()
        self.last_used = time.time()
        self.requests = 0
        self.engine = voice.get("engine", "piper")
        self.proc = subprocess.Popen(
            [python, str(worker_py), voice["path"], self.engine],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0,
        )
        header = self._readline(timeout=180)
        info = json.loads(header)
        if not info.get("ready"):
            self.kill()
            raise VoiceUnavailable(f"voice worker failed to start: {info.get('error')}")
        self.load_seconds = info.get("load_seconds")
        self.started = time.time()

    # -- plumbing ---------------------------------------------------------
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
            raise VoiceUnavailable(
                "voice worker stopped responding"
                + (f": {err.decode('utf-8', 'replace')[:200]}" if err else "")
            )
        return line

    def _read_exact(self, n: int, timeout: float) -> bytes:
        guard = threading.Timer(timeout, self.kill)
        guard.start()
        try:
            chunks, got = [], 0
            while got < n:
                block = self.proc.stdout.read(n - got)
                if not block:
                    raise VoiceUnavailable("voice worker closed mid-audio")
                chunks.append(block)
                got += len(block)
            return b"".join(chunks)
        finally:
            guard.cancel()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def synthesize(self, text: str, length_scale: float, volume: float,
                   timeout: float) -> bytes:
        with self.lock:
            if not self.alive():
                raise VoiceUnavailable("voice worker is not running")
            req = json.dumps({"text": text, "length_scale": length_scale,
                              "volume": volume}).encode("utf-8")
            self.proc.stdin.write(req + b"\n")
            self.proc.stdin.flush()
            head = json.loads(self._readline(timeout))
            if not head.get("ok"):
                raise VoiceUnavailable(f"Piper failed: {head.get('error')}")
            data = self._read_exact(int(head["bytes"]), timeout)
            self.last_used = time.time()
            self.requests += 1
            self.synth_seconds = head.get("seconds")
            residency.touch("voice", self.voice_id)
            return data

    def kill(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.kill()
        except (OSError, ValueError):
            pass


_pool: dict[str, _Worker] = {}
_pool_lock = threading.Lock()
_reaper: threading.Thread | None = None


def _max_resident(cfg: dict[str, Any]) -> int:
    explicit = (cfg.get("speech", {}) or {}).get("max_resident_voices")
    if explicit:
        return max(1, int(explicit))
    return {"constrained": 1, "standard": 2, "roomy": 3}[hardware.profile()["tier"]]


def _idle_timeout(cfg: dict[str, Any]) -> float:
    return float((cfg.get("speech", {}) or {}).get("worker_idle_s") or 300)


def _start_reaper(cfg: dict[str, Any]) -> None:
    """Give memory back when nobody is listening."""
    global _reaper
    if _reaper is not None and _reaper.is_alive():
        return

    def loop() -> None:
        while True:
            time.sleep(30)
            cutoff = time.time() - _idle_timeout(cfg)
            with _pool_lock:
                for vid, w in list(_pool.items()):
                    if w.last_used < cutoff or not w.alive():
                        w.kill()
                        residency.release("voice", vid)
                        del _pool[vid]

    _reaper = threading.Thread(target=loop, daemon=True, name="voice-reaper")
    _reaper.start()


def _acquire(cfg: dict[str, Any], voice: dict[str, Any]) -> _Worker:
    python = _worker_python(cfg)
    if not python:
        raise VoiceUnavailable("no interpreter with piper-tts; run setup-voice.bat")
    worker_py = Path(__file__).parent / "voice_worker.py"

    with _pool_lock:
        w = _pool.get(voice["id"])
        if w is not None and w.alive():
            residency.touch("voice", voice["id"])
            return w
        if w is not None:
            del _pool[voice["id"]]
        # This pool's own cap, then the shared one. The shared budget is what
        # stops a voice and a 1.2 GB recogniser from being resident together on
        # a machine that cannot hold both.
        while len(_pool) >= _max_resident(cfg):
            oldest = min(_pool.values(), key=lambda x: x.last_used)
            oldest.kill()
            residency.release("voice", oldest.voice_id)
            del _pool[oldest.voice_id]

    residency.make_room(voice.get("size_mb") or 100, "voice", voice["id"])
    fresh = _Worker(voice, python, worker_py)      # outside the lock: slow
    with _pool_lock:
        _pool[voice["id"]] = fresh
    residency.register("voice", voice["id"], voice.get("size_mb") or 100,
                       fresh.kill)
    _start_reaper(cfg)
    return fresh


def pool_status() -> list[dict[str, Any]]:
    with _pool_lock:
        return [{
            "voice": w.voice_id,
            "alive": w.alive(),
            "requests": w.requests,
            "load_seconds": w.load_seconds,
            "idle_seconds": round(time.time() - w.last_used),
        } for w in _pool.values()]


def shutdown() -> None:
    with _pool_lock:
        for w in _pool.values():
            w.kill()
            residency.release("voice", w.voice_id)
        _pool.clear()


# ---------------------------------------------------------------- public API

def status(cfg: dict[str, Any]) -> dict[str, Any]:
    cmd = _piper_cmd(cfg)
    python = _worker_python(cfg)
    cat = catalogue(cfg)
    by_lang: dict[str, dict[str, bool]] = {}
    for lang in ("en", "bem", "loz"):
        by_lang[lang] = {
            gender: bool(resolve_voice(cfg, None, lang, gender))
            for gender in ("female", "male")
        }
    # Say where we looked. "Not installed" with no path is a dead end for
    # whoever has to fix it.
    searched = [
        {"where": label, "path": argv[0], "found": _runnable(argv)}
        for label, argv in _candidates(cfg)
    ]
    return {
        "engine": "piper",
        "engine_found": bool(cmd or python),
        "engine_path": " ".join(cmd) if cmd else None,
        "mode": "resident" if python else ("one-shot" if cmd else "unavailable"),
        "mode_note": (
            "Voices stay loaded in memory between requests."
            if python else
            "Each sentence reloads the model from disk - slow. Run setup-voice.bat."
        ),
        "worker_python": python,
        "max_resident": _max_resident(cfg) if python else 0,
        "idle_timeout_s": _idle_timeout(cfg) if python else None,
        "resident": pool_status(),
        "searched": searched,
        "voices_dir": str(voices_dir(cfg)),
        "catalogue": cat,
        "paces": [{"id": k, **v} for k, v in PACES.items()],
        "voices": by_lang,
        "language_reality": {
            code: {**LANGUAGE_TTS_REALITY.get(code, {}),
                   "installed": bool(resolve_voice(cfg, None, code, "female")
                                     or resolve_voice(cfg, None, code, "male"))}
            for code in ("en", "bem", "loz")
        },
        "mms_dir": str(mms_dir(cfg)),
        "note": (
            "Piper ships no Bemba or Lozi voice. Until a voice file is supplied "
            "the tutor stays silent in those languages rather than reading them "
            "with an English voice."
        ),
    }


def _speak_oneshot(text: str, cfg: dict[str, Any], voice: dict[str, Any],
                   length_scale: float) -> bytes:
    """Fallback: a fresh Piper per sentence. Correct, just slow."""
    if voice.get("engine") == "mms":
        raise VoiceUnavailable(
            "MMS voices need the resident worker (torch + transformers in the "
            "voice venv). Piper's command line cannot load them."
        )
    cmd = _piper_cmd(cfg)
    if not cmd:
        raise VoiceUnavailable(
            "Piper is not installed on this machine. Run setup-voice.bat, or set "
            "speech.piper_exe / speech.piper_cmd in data/config.json - the System "
            "check page lists every location that was tried."
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "speech.wav"
        proc = subprocess.run(
            [*cmd, "--model", voice["path"], "--output_file", str(out),
             "--length-scale", str(length_scale)],
            input=text.encode("utf-8"), capture_output=True, timeout=180,
        )
        if proc.returncode != 0 or not out.exists():
            raise VoiceUnavailable(
                "Piper failed: "
                + (proc.stderr.decode("utf-8", "replace")[:200] or "unknown error")
            )
        return out.read_bytes()


def speak(text: str, cfg: dict[str, Any], language: str = "en",
          gender: str = "female", voice_id: str | None = None,
          pace: str = DEFAULT_PACE) -> bytes:
    """Synthesise WAV bytes, or raise VoiceUnavailable with a usable reason."""
    if not text or not text.strip():
        raise VoiceUnavailable("Nothing to read.")
    voice = resolve_voice(cfg, voice_id, language, gender)
    if voice is None:
        raise VoiceUnavailable(
            f"No {gender} voice is installed for {language!r} on this machine."
        )
    length_scale = PACES.get(pace, PACES[DEFAULT_PACE])["length_scale"]
    volume = float((cfg.get("speech", {}) or {}).get("volume", 1.0))
    timeout = float((cfg.get("speech", {}) or {}).get("timeout_s") or 180)

    if _worker_python(cfg):
        try:
            return _acquire(cfg, voice).synthesize(text, length_scale, volume, timeout)
        except VoiceUnavailable:
            # A crashed worker should cost one retry, not the feature. Drop it
            # and fall through to the slow path rather than failing outright.
            with _pool_lock:
                dead = _pool.pop(voice["id"], None)
            if dead:
                dead.kill()
            # ...but only Piper has a one-shot path. Falling through with an MMS
            # voice replaces the real reason ("the worker died", "it ran out of
            # memory") with a confusing one about Piper's command line, and the
            # person reading it then debugs the wrong thing.
            if voice.get("engine") == "mms" or not _piper_cmd(cfg):
                raise
    return _speak_oneshot(text, cfg, voice, length_scale)
