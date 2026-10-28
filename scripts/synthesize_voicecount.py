"""Synthesize voicecount requests offline with Kokoro (kokoro-onnx, Apache-2.0 weights).

Runs in a separate tools environment (kokoro-onnx 0.6.1), not the product
environment. Reads controller_truth/scripts.json, writes one mono 16-bit
24 kHz WAV per item into worker_inputs/media/. No network access.

Usage: python synthesize_voicecount.py <dataset_root> <kokoro.onnx> <voices.bin>
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np
from kokoro_onnx import Kokoro


def main():
    root, model, voices = (Path(arg) for arg in sys.argv[1:4])
    scripts = json.loads((root / "controller_truth" / "scripts.json").read_text(encoding="utf-8"))
    engine = Kokoro(str(model), str(voices))
    for sample_id, script in sorted(scripts.items()):
        samples, rate = engine.create(script["text"], voice=script["voice"], speed=1.0, lang="en-us")
        if rate != 24000:
            raise SystemExit(f"unexpected sample rate {rate}")
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        with wave.open(str(root / "worker_inputs" / "media" / f"{sample_id}.wav"), "wb") as clip:
            clip.setnchannels(1)
            clip.setsampwidth(2)
            clip.setframerate(rate)
            clip.writeframes(pcm)
        print(sample_id, f"{len(samples) / rate:.1f}s", flush=True)


if __name__ == "__main__":
    main()
