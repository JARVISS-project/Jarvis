"""Existing frozen public audio only; never open capture or invoke Tools."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import wave


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    actual = hashlib.sha256(args.wav.read_bytes()).hexdigest()
    if actual != manifest["public_wav"]["sha256"]:
        print(json.dumps({"status": "blocked_wav_identity"}))
        return 1
    import numpy as np
    from jarvis.activation.openwakeword import OpenWakeWordDetector
    with wave.open(str(args.wav), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
        pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    detector = OpenWakeWordDetector()
    start = time.perf_counter()
    detections = 0
    scores = []
    try:
        for index in range(0, len(pcm), 1280):
            frame = np.pad(pcm[index:index + 1280], (0, max(0, 1280-len(pcm[index:index+1280]))))
            detections += int(detector.process(frame.tolist()))
            scores.append(detector.last_base_score)
    finally:
        detector.close()
    print(json.dumps({"frames": len(scores), "detections": detections,
                      "maximum_score": max(scores), "seconds": time.perf_counter()-start,
                      "scope": "one public non-wake clip; not wake recall/accuracy",
                      "microphone": False}))
    return int(detections != 0)


if __name__ == "__main__":
    raise SystemExit(main())
