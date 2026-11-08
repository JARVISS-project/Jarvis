from jarvis.runtime.resource_manager import ResourceManager
from jarvis.speech.base import TranscriptionResult
from tests.integration.test_delegation_23br import configured, approve
from tests.voice.test_pipeline import (
    FakeListener, FakeRecorder, FakeSpeechToText, FakeTextToSpeech, activation, make_pipeline,
)


def test_synthetic_audio_fake_transcription_uses_same_bound_delegation_flow(tmp_path, synthetic_voice_wav):
    import av
    with av.open(str(synthetic_voice_wav)) as audio:
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        pcm = []
        for frame in audio.decode(audio=0):
            for part in resampler.resample(frame):
                pcm.extend(part.to_ndarray().flatten().tolist())
        for part in resampler.resample(None):
            pcm.extend(part.to_ndarray().flatten().tolist())
    assert len(pcm) == 16000 and any(pcm)
    h, admin, s, calls = configured(tmp_path)
    stt = FakeSpeechToText(TranscriptionResult('Delegar para openai: Explain gravity simply.', 'pt', 1.0))
    sink, output = FakeTextToSpeech(), []
    pipeline = make_pipeline(ResourceManager(), FakeListener([activation()]), FakeRecorder(pcm), stt, h, output, tts=sink)
    try:
        assert pipeline.process_once() and stt.calls and not calls
        assert h.current_confirmation_context()
        assert 'aprovação' in h.confirmation_sessions.current().prompt_text
        assert not any('Explain gravity' in line for line in output)
        assert 'concluída' in approve(h) and len(calls) == 1
    finally:
        pipeline.close()
