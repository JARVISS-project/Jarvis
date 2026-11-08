from threading import Event
from uuid import uuid4
from jarvis.application.text_interaction import TextInteractionController
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.speech.base import TranscriptionResult
from tests.integration.test_routines_23bq import configured
from tests.voice.test_pipeline import (
    FakeListener, FakeRecorder, FakeSpeechToText, FakeTextToSpeech, activation, make_pipeline,
)


def test_text_worker_routine_executor_response_progress_and_replay(tmp_path):
    h,tool=configured(tmp_path)
    controller=TextInteractionController(h)
    done=Event();events=[]
    def reply(event):
        events.append(event)
        if event['status'] in {'done','error','cancelled'}:done.set()
    payload={'command':'text_submit','generation':controller.generation,'interaction':uuid4().hex,'text':'modo estudo'}
    try:
        controller.handle('owner',payload,reply)
        assert done.wait(5)
        assert tool.calls==2 and events[-1]['status']=='done'
        assert any('etapa 1/2' in e.get('text','') for e in events)
        controller.handle('owner',payload,reply)
        assert tool.calls==2 and events[-1]['status']=='error'
    finally:controller.close()


def test_synthetic_wav_fake_stt_shared_voice_executor_response(tmp_path, synthetic_voice_wav):
    # Synthetic tone and fake STT test the flow, not recognition or voice quality.
    import av
    with av.open(str(synthetic_voice_wav)) as audio:
        resampler=av.AudioResampler(format='s16',layout='mono',rate=16000)
        pcm=[]
        for frame in audio.decode(audio=0):
            for part in resampler.resample(frame):pcm.extend(part.to_ndarray().flatten().tolist())
        for part in resampler.resample(None):pcm.extend(part.to_ndarray().flatten().tolist())
    assert len(pcm) == 16000 and any(pcm)
    h,tool=configured(tmp_path)
    stt=FakeSpeechToText(TranscriptionResult('Modo estudo.', 'pt', 1.0))
    sink=FakeTextToSpeech();output=[]
    pipeline=make_pipeline(ResourceManager(),FakeListener([activation()]),FakeRecorder(pcm),stt,h,output,tts=sink)
    try:
        assert pipeline.process_once()
        assert stt.calls and tool.calls==2
        assert sink.spoken and 'concluída' in sink.spoken[-1]
    finally:pipeline.close()
