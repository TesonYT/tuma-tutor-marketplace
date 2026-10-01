"""A speech-recognition model, held open. Runs under the voice virtualenv.

The sibling of `voice_worker.py`, and for the same reason: loading a 1.2 GB
wav2vec2 checkpoint per utterance would make speaking to Ethel unusable. The
model is loaded once and every later request is inference only.

What this is for: letting a student *speak* Bemba, Lozi, Tonga or Nyanja to
Ethel. Zambezi Voice (huggingface.co/zambezivoice, Apache 2.0) publishes
recognition models for exactly those languages, and they are the only credible
option for them. Note the direction - these models listen, they do not speak.
Nothing here can read a lesson aloud.

Protocol - length-prefixed WAV in, JSON out:

    -> {"bytes": 91234}\\n  followed by 91234 bytes of 16 kHz mono WAV
    <- {"ok": true, "text": "...", "seconds": 1.8}
    <- {"ok": false, "error": "..."}

Audio arrives as 16 kHz mono PCM WAV because the browser encodes it that way
(see `recordWav` in app.js). That avoids needing ffmpeg on the machine, which
would be one more thing to install offline for no benefit.
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


def _read_wav_mono16k(data: bytes):
    """WAV bytes -> float32 samples at 16 kHz, which is what these models want."""
    import numpy as np

    with wave.open(io.BytesIO(data), "rb") as w:
        rate = w.getframerate()
        channels = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit audio, got {width * 8}-bit")
    samples = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        # Linear resample. Good enough for speech, and avoids depending on
        # scipy or librosa for a machine that installs as little as possible.
        n = int(round(len(samples) * 16000 / rate))
        if n <= 0:
            raise ValueError("audio too short")
        idx = np.linspace(0, len(samples) - 1, n)
        samples = np.interp(idx, np.arange(len(samples)), samples).astype("float32")
    return samples


def _load(model_dir: str):
    """Return transcribe(samples) -> str for whichever architecture is here.

    Zambezi Voice publishes both wav2vec2/XLS-R and Whisper fine-tunes, and they
    are driven differently, so the checkpoint's own config decides.
    """
    import os

    # CTranslate2 layout (faster-whisper): model.bin beside vocabulary.txt.
    # This is the fast path and the only one that makes a spoken loop usable -
    # base.en measures ~3.6s for 4.4s of audio here, against ~20s for the 1.2 GB
    # transformers checkpoints. Checked first, and it needs neither torch nor
    # transformers.
    if os.path.exists(os.path.join(model_dir, "model.bin")):
        from faster_whisper import WhisperModel

        model = WhisperModel(model_dir, device="cpu", compute_type="int8")

        def transcribe(samples) -> str:
            segments, _ = model.transcribe(samples, beam_size=1)
            return "".join(seg.text for seg in segments).strip()

        return transcribe, "faster-whisper"

    import torch
    from transformers import AutoConfig

    cfg = AutoConfig.from_pretrained(model_dir)
    kind = getattr(cfg, "model_type", "")

    if kind in ("whisper",):
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        proc = WhisperProcessor.from_pretrained(model_dir)
        model = WhisperForConditionalGeneration.from_pretrained(model_dir).eval()

        def transcribe(samples) -> str:
            feats = proc(samples, sampling_rate=16000, return_tensors="pt").input_features
            with torch.no_grad():
                ids = model.generate(feats, max_new_tokens=200)
            return proc.batch_decode(ids, skip_special_tokens=True)[0].strip()

        return transcribe, kind

    from transformers import AutoModelForCTC

    # Several Zambezi Voice checkpoints ship a 5-gram language model beside the
    # acoustic one, and AutoProcessor then insists on pyctcdecode + kenlm. That
    # combination does not install cleanly everywhere (kenlm needs a C++ build
    # on Windows), and refusing to load at all because a *bonus* decoder is
    # missing would be the wrong trade: without the LM the model still works,
    # just less accurately. So try the good path, fall back to the plain one,
    # and report which was used rather than hiding the difference.
    has_lm = False
    try:
        from transformers import AutoProcessor

        proc = AutoProcessor.from_pretrained(model_dir)
        has_lm = getattr(proc, "decoder", None) is not None
    except ImportError:
        from transformers import Wav2Vec2Processor

        proc = Wav2Vec2Processor.from_pretrained(model_dir)
    model = AutoModelForCTC.from_pretrained(model_dir).eval()

    def transcribe(samples) -> str:
        inputs = proc(samples, sampling_rate=16000, return_tensors="pt")
        with torch.no_grad():
            logits = model(**inputs).logits
        if has_lm:
            return proc.batch_decode(logits.numpy()).text[0].strip()
        ids = torch.argmax(logits, dim=-1)
        return proc.batch_decode(ids)[0].strip()

    return transcribe, ("wav2vec2+lm" if has_lm else "wav2vec2")


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ready": False, "error": "no model path given"}), flush=True)
        return 2
    model_dir = sys.argv[1]
    stdin, stdout = _binary_stdio()

    def send(obj: dict) -> None:
        stdout.write(json.dumps(obj).encode("utf-8") + b"\n")
        stdout.flush()

    try:
        t0 = time.time()
        transcribe, kind = _load(model_dir)
        load_s = time.time() - t0
    except Exception as exc:  # noqa: BLE001
        send({"ready": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1

    send({"ready": True, "model": os.path.basename(model_dir.rstrip("/\\")),
          "kind": kind, "load_seconds": round(load_s, 2)})

    while True:
        line = stdin.readline()
        if not line:
            return 0
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
        n = int(req.get("bytes") or 0)
        if n <= 0:
            send({"ok": False, "error": "no audio"})
            continue

        chunks, got = [], 0
        while got < n:
            block = stdin.read(n - got)
            if not block:
                return 1
            chunks.append(block)
            got += len(block)
        audio = b"".join(chunks)

        try:
            t0 = time.time()
            samples = _read_wav_mono16k(audio)
            if len(samples) < 1600:            # under a tenth of a second
                send({"ok": False, "error": "that was too short to hear"})
                continue
            text = transcribe(samples)
            send({"ok": True, "text": text,
                  "audio_seconds": round(len(samples) / 16000, 2),
                  "seconds": round(time.time() - t0, 3)})
        except Exception as exc:  # noqa: BLE001
            send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    sys.exit(main())
