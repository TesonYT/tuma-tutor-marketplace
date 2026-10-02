"""A Piper voice, held open.

This file is the odd one out in the project: it is the only module that runs
under the voice virtualenv's interpreter rather than the system one, because it
is the only code that imports `piper`. Ethel's server never imports it - it
spawns it, one process per loaded voice, and talks to it over a pipe.

Why it exists: the obvious way to use Piper is to shell out per sentence, and
that reloads a 63 MB model from disk every single time. Measured on a 2-core
machine that was 17-20 seconds for one short sentence with no warm-up benefit,
which makes voice a novelty rather than something usable during a lesson. Here
the model is loaded once at start-up and every later request is pure synthesis.

Protocol - newline-delimited JSON in, length-prefixed WAV out:

    -> {"text": "...", "length_scale": 1.0, "volume": 1.0}
    <- {"ok": true, "bytes": 91234, "seconds": 0.42}\\n  followed by 91234 raw bytes
    <- {"ok": false, "error": "..."}\\n                  on failure, no bytes

One header line is written on start-up so the parent knows loading finished:

    <- {"ready": true, "voice": "en_GB-alan-medium", "load_seconds": 3.1, ...}

stdout carries binary, so it is put into binary mode explicitly - on Windows the
default text mode would corrupt any 0x0A byte in the audio.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import wave


def _binary_stdio() -> tuple[object, object]:
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    return sys.stdin.buffer, sys.stdout.buffer


def _load_piper(model_path: str):
    """Piper: the English voices. Returns synthesize(text, length_scale, volume)."""
    from piper import PiperVoice, SynthesisConfig

    voice = PiperVoice.load(model_path)

    def synthesize(text: str, length_scale, volume: float) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(
                text, wav,
                syn_config=SynthesisConfig(length_scale=length_scale, volume=volume),
            )
        return buf.getvalue()

    return synthesize


def _load_mms(model_dir: str):
    """Meta MMS-TTS: the only route to a Bemba voice.

    Piper publishes no Bemba or Lozi voice. Meta's MMS covers Bemba (`bem`), so
    a downloaded `facebook/mms-tts-bem` can be run locally here. It needs torch
    and transformers, which is why this is a separate branch rather than a
    dependency of the whole app - most installs will never want it.

    There is no Lozi model in MMS, and none anywhere on the Hub that I could
    find. Lozi audio has to be recorded by a person; the pack format supports
    that and `speech.py` prefers a recording over synthesis wherever one exists.

    MMS-TTS models are VITS, so `length_scale` maps onto `speaking_rate` the
    same way Piper's does - inverted, because MMS expresses it as a rate.
    """
    import numpy as np
    import torch
    from transformers import AutoTokenizer, VitsModel

    model = VitsModel.from_pretrained(model_dir)
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model.eval()
    rate = int(model.config.sampling_rate)

    def synthesize(text: str, length_scale, volume: float) -> bytes:
        if length_scale:
            # Piper: >1 is slower. MMS: speaking_rate <1 is slower.
            model.speaking_rate = 1.0 / float(length_scale)
        inputs = tokenizer(text, return_tensors="pt")
        with torch.no_grad():
            wav = model(**inputs).waveform[0].cpu().numpy()
        if volume and volume != 1.0:
            wav = wav * float(volume)
        pcm = np.clip(wav, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        return buf.getvalue()

    return synthesize


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ready": False, "error": "no model path given"}), flush=True)
        return 2
    model_path = sys.argv[1]
    stdin, stdout = _binary_stdio()

    def send(obj: dict, payload: bytes = b"") -> None:
        stdout.write(json.dumps(obj).encode("utf-8") + b"\n")
        if payload:
            stdout.write(payload)
        stdout.flush()

    engine = sys.argv[2] if len(sys.argv) > 2 else "piper"

    try:
        t0 = time.time()
        if engine == "mms":
            synth = _load_mms(model_path)
        else:
            synth = _load_piper(model_path)
        load_s = time.time() - t0
    except Exception as exc:  # noqa: BLE001 - report anything, then exit
        send({"ready": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1

    send({
        "ready": True,
        "voice": os.path.splitext(os.path.basename(model_path.rstrip("/\\")))[0],
        "engine": engine,
        "load_seconds": round(load_s, 2),
    })

    while True:
        line = stdin.readline()
        if not line:
            return 0                      # parent closed the pipe
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError as exc:
            send({"ok": False, "error": f"bad request: {exc}"})
            continue
        if req.get("shutdown"):
            return 0

        text = (req.get("text") or "").strip()
        if not text:
            send({"ok": False, "error": "empty text"})
            continue

        try:
            t0 = time.time()
            data = synth(text, req.get("length_scale"), float(req.get("volume", 1.0)))
            send({"ok": True, "bytes": len(data), "seconds": round(time.time() - t0, 3)}, data)
        except Exception as exc:  # noqa: BLE001 - one bad line must not kill the voice
            send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    sys.exit(main())
