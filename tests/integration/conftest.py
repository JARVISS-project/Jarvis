"""Reproducible synthetic inputs; no microphone, models or private recordings."""
import math
import struct
import wave

import pytest


@pytest.fixture
def synthetic_voice_wav(tmp_path):
    """Exercise WAV decoding/resampling; transcription remains explicitly fake."""
    path = tmp_path / 'synthetic-tone.wav'
    rate = 22050
    frames = bytearray()
    for index in range(rate):
        sample = round(4000 * math.sin(2 * math.pi * 440 * index / rate))
        frames.extend(struct.pack('<hh', sample, sample))
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return path
