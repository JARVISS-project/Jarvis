import numpy as np

from jarvis.tts.playback_activity import compute_activity_envelope, play_with_activity


def test_activity_envelope_tracks_real_pcm_intensity() -> None:
    sample_rate = 1000
    quiet = np.full(30, 0.02, dtype=np.float32)
    loud = np.full(30, 0.20, dtype=np.float32)
    silence = np.zeros(30, dtype=np.float32)
    audio = np.concatenate((quiet, loud, silence))

    levels = compute_activity_envelope(
        audio,
        sample_rate,
        window_seconds=0.03,
    )

    assert len(levels) == 3
    assert levels[1] > levels[0] > levels[2]
    assert levels[2] == 0.0
    assert 0.0 <= min(levels) <= max(levels) <= 1.0


def test_activity_envelope_silent_audio_stays_zero() -> None:
    levels = compute_activity_envelope(
        np.zeros(90, dtype=np.float32),
        1000,
        window_seconds=0.03,
    )

    assert levels == [0.0, 0.0, 0.0]


class FakeSoundDevice:
    def __init__(self):
        self.play_called = False
        self.stop_called = False
        self.wait_called = False

    def play(self, audio, samplerate, blocking):
        self.play_called = True

    def stop(self):
        self.stop_called = True

    def wait(self):
        self.wait_called = True

def test_play_with_activity_cancel_during_activity():
    sd = FakeSoundDevice()
    import numpy as np
    audio = np.full(3000, 0.5, dtype=np.float32) # 3 seconds at 1000Hz = 3000 samples

    stop_event = False

    levels_received = []
    def callback(l):
        levels_received.append(l)
        nonlocal stop_event
        if len(levels_received) >= 2:
            stop_event = True

    play_with_activity(sd, audio, 1000, level_callback=callback, stop_requested=lambda: stop_event)

    assert sd.play_called
    assert sd.stop_called
    assert not sd.wait_called
    assert levels_received[-1] == 0.0 # finally block
    assert len(levels_received) <= 4

def test_play_with_activity_natural_completion():
    sd = FakeSoundDevice()
    import numpy as np
    audio = np.full(100, 0.5, dtype=np.float32) # 0.1 seconds at 1000Hz

    levels_received = []
    play_with_activity(sd, audio, 1000, level_callback=levels_received.append)

    assert sd.play_called
    assert not sd.stop_called
    assert sd.wait_called
    assert levels_received[-1] == 0.0

def test_play_with_activity_stop_already_requested():
    sd = FakeSoundDevice()
    import numpy as np
    audio = np.full(3000, 0.5, dtype=np.float32)

    levels_received = []
    play_with_activity(sd, audio, 1000, level_callback=levels_received.append, stop_requested=lambda: True)

    assert sd.play_called
    assert sd.stop_called
    assert not sd.wait_called
    assert levels_received == [0.0]

def test_play_with_activity_double_stop():
    sd = FakeSoundDevice()
    import numpy as np
    audio = np.full(3000, 0.5, dtype=np.float32)

    # Simulate an external stop that raises no exception
    def broken_stop():
        sd.stop_called = True
    sd.stop = broken_stop

    levels_received = []
    play_with_activity(sd, audio, 1000, level_callback=levels_received.append, stop_requested=lambda: True)

    assert sd.stop_called
    assert not sd.wait_called
