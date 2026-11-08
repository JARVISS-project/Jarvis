"""Real listener/recorder/session/pipeline and TCP, with deterministic PCM/TTS."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import socket
import time

import pytest

from jarvis.activation.listener import WakeWordListener
from jarvis.application.state import AgentStateBus
from jarvis.application.state_ipc import LocalStateClient, LocalStateServer
from jarvis.audio.utterance import UtteranceRecorder
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.speech.validator import TranscriptionValidator
from jarvis.tts.base import SpeechPlaybackHandle
from jarvis.voice.audio_session import VoiceAudioSession
from jarvis.voice.pipeline import VoicePipeline
from jarvis.voice.ptt import PttState
from tests.voice.test_pipeline import FakeHarness, FakeSpeechToText
from jarvis.speech.base import TranscriptionResult


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "Timed out"
        time.sleep(0.005)


class Audio:
    def __init__(self):
        self.started = False
        self.closed = False
        self.reads = 0
        self.on_read = lambda: None

    def start(self):
        assert not self.closed
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.stop()
        self.closed = True

    def read_frame(self):
        assert self.started
        self.reads += 1
        self.on_read()
        return [2000] * 512


class Detector:
    def __init__(self):
        self.calls = 0

    def process(self, frame):
        self.calls += 1
        return False

    def reset(self):
        pass

    def close(self):
        pass


class Playback:
    def __init__(self):
        self.started = Event()
        self.finished = Event()
        self.stopped = Event()

    def speak_async(self, text):
        self.started.set()
        return SpeechPlaybackHandle(self.finished, self.stop)

    def stop(self):
        self.stopped.set()
        self.finished.set()

    def close(self):
        self.stop()


def build_pipeline(**kwargs):
    audio = Audio()
    listener = WakeWordListener(audio, Detector())
    recorder = UtteranceRecorder(audio, minimum_speech_duration_seconds=0.01)
    pipeline = VoicePipeline(
        listener, recorder, FakeSpeechToText(), kwargs.pop("harness", FakeHarness()),
        ResourceManager(), audio_session=VoiceAudioSession(listener),
        cooldown_waiter=kwargs.pop("cooldown_waiter", lambda _: None),
        output=lambda _: None, **kwargs,
    )
    listener.start()
    return pipeline, audio


def press(pipeline):
    pipeline.handle_ui_command({"command": "ptt_start"})


def release(pipeline):
    pipeline.handle_ui_command({"command": "ptt_stop"})


def test_ptt_without_wake_records_until_release_and_uses_normal_pipeline():
    pipeline, audio = build_pipeline()
    audio.on_read = lambda: release(pipeline) if audio.reads == 5 else None
    press(pipeline)
    assert pipeline.process_once()
    assert audio.reads == 5
    assert pipeline.listener.detector.calls == 0
    assert pipeline.recorder.last_stop_reason == "ptt_release"
    assert pipeline.harness.calls == [("comando", False)]
    assert pipeline.audio_session.owner is None
    assert audio.started
    pipeline.close()


def test_quick_press_release_reads_no_audio_and_never_calls_harness():
    pipeline, audio = build_pipeline()
    press(pipeline)
    release(pipeline)
    press(pipeline)  # Cannot erase this session's release.
    pipeline.process_once()
    assert audio.reads == 0
    assert not pipeline.harness.calls
    assert pipeline.ptt.state is PttState.COMPLETED
    pipeline.close()


def test_ptt_adopts_an_existing_capture():
    pipeline, audio = build_pipeline()
    def on_read():
        if audio.reads == 1:
            press(pipeline)
            press(pipeline)
        elif audio.reads == 4:
            release(pipeline)
    audio.on_read = on_read
    pipeline._process_recording(pipeline.recorder)
    assert audio.reads == 4
    assert len(pipeline.harness.calls) == 1
    assert pipeline.audio_session.owner is None
    pipeline.close()


def test_ptt_interrupts_playback_before_acquiring_usable_microphone():
    playback = Playback()
    pipeline, audio = build_pipeline(text_to_speech=playback)
    with ThreadPoolExecutor(1) as pool:
        spoken = pool.submit(pipeline._speak_response, "Resposta longa")
        assert playback.started.wait(2)
        assert not audio.started
        assert pipeline.audio_session.owner == "playback"
        press(pipeline)
        assert playback.stopped.wait(2)
        assert spoken.result(2)
    assert audio.started
    pipeline.tts_enabled = False
    audio.on_read = lambda: release(pipeline)
    pipeline.process_once()
    assert len(pipeline.harness.calls) == 1
    assert pipeline.audio_session.owner is None
    pipeline.close()


def test_echo_guard_keeps_input_stopped_through_playback_and_cooldown():
    playback = Playback()
    observed = []
    pipeline, audio = build_pipeline(
        text_to_speech=playback,
        cooldown_waiter=lambda seconds: observed.append((seconds, audio.started)),
    )
    with ThreadPoolExecutor(1) as pool:
        spoken = pool.submit(pipeline._speak_response, "Hey Jarvis")
        assert playback.started.wait(2)
        assert not audio.started
        assert audio.reads == pipeline.listener.detector.calls == 0
        playback.finished.set()
        assert spoken.result(2)
    assert observed == [(0.75, False)]
    assert audio.started
    assert not pipeline.harness.calls
    pipeline.close()


@pytest.mark.parametrize("during", ["capture", "playback"])
def test_shutdown_releases_microphone_and_playback(during):
    playback = Playback()
    pipeline, audio = build_pipeline(text_to_speech=playback)
    if during == "capture":
        audio.on_read = pipeline.request_stop
        press(pipeline)
        pipeline.run()
    else:
        with ThreadPoolExecutor(1) as pool:
            task = pool.submit(pipeline._speak_response, "Resposta")
            assert playback.started.wait(2)
            pipeline.request_stop()
            task.result(2)
        pipeline.close()
    assert audio.closed and not audio.started
    assert playback.stopped.is_set()
    assert pipeline.audio_session.owner is None
    assert pipeline.ptt.state is PttState.CANCELLED
    assert not pipeline.harness.calls


def test_audio_error_completes_session_and_allows_next_press():
    pipeline, audio = build_pipeline()
    def fail():
        raise OSError("device lost")
    audio.on_read = fail
    press(pipeline)
    pipeline.process_once()
    assert pipeline.audio_session.owner is None
    assert pipeline.ptt.state is PttState.COMPLETED
    audio.on_read = lambda: release(pipeline)
    press(pipeline)
    pipeline.process_once()
    assert len(pipeline.harness.calls) == 1
    pipeline.close()


def test_client_server_dispatcher_round_trip_and_disconnect_release(tmp_path):
    pipeline, audio = build_pipeline()
    server = LocalStateServer(AgentStateBus(), endpoint_path=tmp_path / "ipc.json",
                              command_callback=pipeline.handle_ui_command)
    client = LocalStateClient(AgentStateBus(), server.start())
    client.start()
    try:
        wait_for(lambda: client._socket is not None)
        client.send_command({"command": "ptt_start"})
        wait_for(lambda: pipeline.ptt.pending)
        client.send_command({"command": "ptt_stop"})
        wait_for(lambda: pipeline.ptt.stop_requested)
        pipeline.process_once()
        client.send_command({"command": "ptt_start"})
        wait_for(lambda: pipeline.ptt.pending)
        client.close()
        wait_for(lambda: pipeline.ptt.stop_requested)
        pipeline.process_once()
        assert not pipeline.harness.calls
    finally:
        client.close()
        server.close()
        pipeline.close()


@pytest.mark.parametrize("raw", [
    b'{"command":"shell"}\n', b'{"command":"ptt_start","tool":"x"}\n',
    b'{bad json}\n', b'[]\n', b'null\n', b'{"command":{}}\n',
    b'{"command":"ptt_start","command":"ptt_stop"}\n', b'\xff\n',
    b'x' * 257, b'{"command":"ptt_start"}' + b' ' * 256 + b'\n',
])
def test_inbound_invalid_command_cannot_reach_pipeline_or_tool(tmp_path, raw):
    pipeline, _ = build_pipeline()
    calls = []
    def dispatch(payload):
        calls.append(payload)
        pipeline.handle_ui_command(payload)
    server = LocalStateServer(AgentStateBus(), endpoint_path=tmp_path / "ipc.json",
                              command_callback=dispatch)
    endpoint = server.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2) as peer:
            peer.recv(4096)
            peer.sendall(raw)
            try:
                assert peer.recv(4096) == b""
            except ConnectionResetError:
                pass  # Windows resets peers with unread oversized data.
        assert calls == []
        assert not pipeline.ptt.pending
        assert not pipeline.harness.calls
    finally:
        server.close()
        pipeline.close()


def test_fragmented_and_coalesced_ipc_preserves_start_stop(tmp_path):
    calls = []
    server = LocalStateServer(AgentStateBus(), endpoint_path=tmp_path / "ipc.json",
                              command_callback=calls.append)
    endpoint = server.start()
    try:
        with socket.create_connection((endpoint.host, endpoint.port), timeout=2) as peer:
            peer.recv(4096)
            peer.sendall(b'{"command":"ptt_')
            peer.sendall(b'start"}\n{"command":"ptt_stop"}\n')
            wait_for(lambda: len(calls) == 2)
            assert calls == [{"command": "ptt_start"}, {"command": "ptt_stop"}]
    finally:
        server.close()


@pytest.mark.parametrize("answer,blocked,executes", [
    ("sim, pode fazer", False, True), ("não", False, False),
    ("sim, pode fazer", True, False),
])
def test_ptt_uses_real_harness_executor_confirmation_and_preflight(
    tmp_path, answer, blocked, executes,
):
    from tests.integration.test_critical_confirmation_session import build_harness
    from jarvis.application.state import AgentState
    from jarvis.application.tool_execution_state import AgentToolExecutionObserver
    from jarvis.application.voice_bridge import VoiceStateBridge
    from tests.voice.test_pipeline import FakeTextToSpeech

    harness, tool = build_harness(tmp_path)
    state_bus = AgentStateBus()
    states = []
    state_bus.subscribe(states.append, replay_current=False)
    harness.tool_executor.observer = AgentToolExecutionObserver(state_bus)
    pipeline, audio = build_pipeline(
        harness=harness, state_callback=VoiceStateBridge(state_bus),
        text_to_speech=FakeTextToSpeech(),
    )
    pipeline.speech_to_text.result = TranscriptionResult(
        "Execute a ação crítica em target-a.", "pt", 1.0,
    )
    audio.on_read = lambda: release(pipeline)
    try:
        press(pipeline)
        pipeline.process_once()
        assert harness.has_pending_confirmation()
        assert tool.executions == []
        tool.blocked = blocked
        pipeline.speech_to_text.result = TranscriptionResult(answer, "pt", 1.0)
        press(pipeline)
        pipeline.process_once()
        assert bool(tool.executions) is executes
        assert any(s.state is AgentState.EXECUTING for s in states) is executes
        if executes:
            observed = [s.state for s in states]
            execution = observed.index(AgentState.EXECUTING)
            assert AgentState.LISTENING in observed[:execution]
            assert AgentState.PROCESSING in observed[:execution]
            assert AgentState.SPEAKING in observed[execution:]
            assert states[execution].source == "tool_executor"
        assert (tmp_path / "audit.log").exists()
    finally:
        pipeline.close()


def test_ptt_cannot_bypass_transcription_validation():
    pipeline, audio = build_pipeline(transcription_validator=TranscriptionValidator())
    pipeline.speech_to_text.result = TranscriptionResult("", "pt", 0.0)
    audio.on_read = lambda: release(pipeline)
    press(pipeline)
    pipeline.process_once()
    assert not pipeline.harness.calls
    pipeline.close()


def test_ptt_cannot_override_policy_denial(tmp_path):
    from tests.integration.test_critical_confirmation_session import build_harness
    from jarvis.security.policy import SecurityPolicy

    class DenyPolicy(SecurityPolicy):
        def is_allowed(self, risk):
            return False

    harness, tool = build_harness(tmp_path)
    harness.tool_executor.safety_manager.policy = DenyPolicy()
    pipeline, audio = build_pipeline(harness=harness)
    audio.on_read = lambda: release(pipeline)
    pipeline.speech_to_text.result = TranscriptionResult(
        "Execute a ação crítica em target-a.", "pt", 1.0,
    )
    try:
        press(pipeline)
        pipeline.process_once()
        pipeline.speech_to_text.result = TranscriptionResult("sim, pode fazer", "pt", 1.0)
        press(pipeline)
        pipeline.process_once()
        assert tool.executions == []
        assert (tmp_path / "audit.log").exists()
    finally:
        pipeline.close()


def test_confirmation_playback_uses_same_ptt_and_serialized_capture():
    from jarvis.security.confirmation import VoiceConfirmation

    playback = Playback()
    pipeline, audio = build_pipeline()
    confirmation = VoiceConfirmation(
        pipeline.listener, pipeline.recorder, pipeline.speech_to_text,
        playback, pipeline.resource_manager, audio_session=pipeline.audio_session,
        cooldown_waiter=lambda _: None, output=lambda _: None,
    )
    audio.on_read = lambda: release(pipeline)
    with ThreadPoolExecutor(1) as pool:
        task = pool.submit(confirmation._speak_then_capture, "Autoriza?", pipeline.recorder)
        assert playback.started.wait(2)
        assert pipeline.audio_session.owner == "confirmation"
        assert not audio.started
        press(pipeline)
        assert task.result(2) == [2000] * 512
    assert playback.stopped.is_set()
    assert pipeline.audio_session.owner is None
    assert not audio.started
    assert not pipeline.harness.calls
    pipeline.close()


def test_playback_error_restores_stream_and_releases_owner():
    class BrokenPlayback(Playback):
        def speak_async(self, text):
            raise OSError("output device unavailable")

    pipeline, audio = build_pipeline(text_to_speech=BrokenPlayback())
    pipeline._speak_response("Resposta")
    assert audio.started
    assert pipeline.audio_session.owner is None
    pipeline.close()


def test_failed_playback_stop_never_reopens_microphone():
    class BrokenStop(Playback):
        def stop(self):
            self.stopped.set()
            raise OSError("stop failed")

        def close(self):
            self.finished.set()

    playback = BrokenStop()
    pipeline, audio = build_pipeline(text_to_speech=playback)
    with ThreadPoolExecutor(1) as pool:
        task = pool.submit(pipeline._speak_response, "Resposta")
        assert playback.started.wait(2)
        press(pipeline)
        with pytest.raises(RuntimeError, match="playback"):
            task.result(2)
    assert not audio.started
    assert pipeline.audio_session.owner is None
    pipeline.close()


def test_socket_press_release_captures_audio_into_normal_pipeline(tmp_path):
    pipeline, audio = build_pipeline()
    server = LocalStateServer(AgentStateBus(), endpoint_path=tmp_path / "ipc.json",
                              command_callback=pipeline.handle_ui_command)
    client = LocalStateClient(AgentStateBus(), server.start())
    client.start()
    def end_capture():
        client.send_command({"command": "ptt_stop"})
        wait_for(lambda: pipeline.ptt.stop_requested)
    audio.on_read = end_capture
    try:
        wait_for(lambda: client._socket is not None)
        client.send_command({"command": "ptt_start"})
        wait_for(lambda: pipeline.ptt.pending)
        pipeline.process_once()
        assert pipeline.harness.calls == [("comando", False)]
        assert pipeline.listener.detector.calls == 0
        assert pipeline.recorder.last_stop_reason == "ptt_release"
    finally:
        client.close()
        server.close()
        pipeline.close()


def test_ptt_ignores_pure_silence():
    pipeline, audio = build_pipeline(transcription_validator=TranscriptionValidator())
    pipeline.speech_to_text.result = TranscriptionResult(
        "", "pt", 0.0, no_speech_probability=1.0,
    )
    audio.on_read = lambda: release(pipeline)

    press(pipeline)
    assert pipeline.ptt.pending

    def custom_read_frame():
        audio.reads += 1
        if audio.reads > 10:
            audio.on_read()
        return [0] * 512
    audio.read_frame = custom_read_frame
    pipeline.process_once()

    assert audio.started
    assert len(pipeline.harness.calls) == 0
    pipeline.close()

def test_ptt_handles_delayed_playback_finished():
    import threading, time
    class DelayedPlayback(Playback):
        def __init__(self):
            super().__init__()
            self.finish_trigger = Event()
        def stop(self):
            self.stopped.set()
            def delayed_finish():
                self.finish_trigger.wait()
                self.finished.set()
            threading.Thread(target=delayed_finish).start()

    playback = DelayedPlayback()
    pipeline, audio = build_pipeline(text_to_speech=playback)
    with ThreadPoolExecutor(1) as pool:
        spoken = pool.submit(pipeline._speak_response, 'Resposta longa')
        assert playback.started.wait(2)
        assert not audio.started
        press(pipeline)
        assert playback.stopped.wait(2)
        time.sleep(0.1)
        assert not spoken.done()
        playback.finish_trigger.set()
        assert spoken.result(2)
    assert audio.started
    pipeline.close()
