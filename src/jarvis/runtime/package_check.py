"""Explicit packaged acceptance. Read-only probes and supplied neutral audio only."""
from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import sys
import threading
import time
import wave

from jarvis.runtime.release import VERSION


def check_stt_config(model: Path, device: str):
    """Explicit CPU acceptance is separate from CUDA acceptance, never fallback."""
    from jarvis.speech.stt_process import SttModelConfig
    if device not in {'cpu', 'cuda'}:
        raise ValueError('Unsupported acceptance device')
    if device == 'cuda':
        from jarvis.speech.gpu_budget import GpuBudgetManager
        GpuBudgetManager().require(5781)
    return SttModelConfig(str(model), device=device,
                          compute_type='float16' if device == 'cuda' else 'int8')


def run_check(root: Path, args) -> None:
    result = {'frozen': bool(getattr(sys, 'frozen', False)), 'version': VERSION,
              'microphone': False, 'playback': False, 'external_effects': False}
    output = root / 'package-check.json'
    h = server = client = text = admin = window = None
    try:
        from PySide6.QtCore import QObject, Signal
        from PySide6.QtWidgets import QApplication
        from jarvis.__main__ import build_harness
        from jarvis.config import JarvisConfig
        from jarvis.application.administration import AdministrationController
        from jarvis.application.conversation_preferences import ConversationPreferences
        from jarvis.application.text_interaction import TextInteractionController, TextEventBus
        from jarvis.application.state import AgentStateBus
        from jarvis.application.state_ipc import LocalStateServer, LocalStateClient
        from jarvis.ui.qt_app import ControlCenterWindow
        from jarvis.runtime.resource_manager import ResourceManager
        from jarvis.voice.semantic_shadow import load_catalog
        from jarvis.runtime.diagnostic import check_core_audio, check_gsmtc
        cfg = JarvisConfig()
        load_catalog()
        result['catalog_integrity'] = True
        h = build_harness(cfg, ResourceManager())
        speech = ConversationPreferences(cfg)
        h.administration.conversation_preferences = speech
        text = TextInteractionController(h, speech=speech)
        admin = AdministrationController(h.administration, text.generation)
        state = AgentStateBus()
        server = LocalStateServer(state, endpoint_path=cfg.ambient_ui_endpoint_path,
                                  text_controller=text, admin_controller=admin)
        endpoint = server.start()
        app = QApplication.instance() or QApplication([])
        class Relay(QObject):
            received = Signal(object)
        relay = Relay()
        events = TextEventBus()
        client = LocalStateClient(state, endpoint, text_bus=events)
        window = ControlCenterWindow(on_command=client.send_command)
        events.subscribe(relay.received.emit)
        relay.received.connect(window.apply_text_event)
        client.start()
        window.show()
        def settle(predicate, timeout=25):
            deadline = time.monotonic() + timeout
            ticks = 0
            while not predicate() and time.monotonic() < deadline:
                app.processEvents()
                ticks += 1
                time.sleep(.01)
            if not predicate():
                raise TimeoutError('Packaged UI check timed out')
            return ticks
        page = window.help_page
        settle(lambda: bool(page.data) and not page.busy)
        assert window.page_stack.currentWidget() is page
        result['ui_help_ipc'] = True
        window.grab().save(str(root / 'tmp' / 'packaged-ui.png'))
        page.diagnose_button.click()
        result['qt_ticks_diagnostic'] = settle(lambda: bool(h.administration.first_use.latest) and not page.busy)
        settle(lambda: not page.busy)
        result['hardware'] = h.administration.first_use.latest
        result['fresh_profile'] = {'google_write': cfg.google_calendar_write_enabled,
                                   'spoken': cfg.tts_enabled,
                                   'microphone_permission': h.administration.permissions.decision('microphone.capture').mode}
        assert not cfg.google_calendar_write_enabled and not cfg.tts_enabled
        assert result['fresh_profile']['microphone_permission'] == 'block'
        result['com_read_only'] = check_core_audio(True)
        result['winrt_read_only'] = check_gsmtc(True)
        import piper, onnxruntime, ctranslate2, winrt.windows.media.control, pvrecorder
        result['native_imports'] = {'piper': True, 'onnxruntime': True, 'ctranslate2': True,
                                   'winrt': True, 'pvrecorder': True}
        for module in (piper, onnxruntime, ctranslate2, pvrecorder):
            assert Path(module.__file__).resolve().is_relative_to(Path(sys._MEIPASS).resolve())
        if args.check_voice:
            from piper.voice import PiperVoice
            voice = PiperVoice.load(str(args.check_voice), config_path=str(args.check_voice) + '.json')
            with wave.open(str(root / 'tmp' / 'neutral-piper.wav'), 'wb') as wav:
                voice.synthesize_wav('Esta é uma prévia neutra da voz local do assistente.', wav)
            result['piper_wav_bytes'] = (root / 'tmp' / 'neutral-piper.wav').stat().st_size
        from jarvis.speech.stt_process import SttModelConfig, SttProcessClient
        bad = SttProcessClient(SttModelConfig(str(root / 'models' / 'missing-model'), device='cpu', compute_type='int8'), startup_timeout_seconds=10, max_restarts=0)
        try:
            bad.warm_up()
            raise AssertionError('Missing model accepted')
        except RuntimeError:
            result['missing_dependency_rejected'] = True
        finally:
            bad.close()
            assert not bad.worker_alive
        if args.check_wav and args.check_stt_model:
            config = check_stt_config(args.check_stt_model, args.check_stt_device)
            import av
            container = av.open(str(args.check_wav))
            resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
            pcm = []
            for frame in container.decode(audio=0):
                for chunk in resampler.resample(frame):
                    pcm.extend(chunk.to_ndarray().flatten().tolist())
            container.close()
            stt = SttProcessClient(config, startup_timeout_seconds=60, timeout_seconds=60, max_restarts=0)
            try:
                stt.warm_up()
                answer = stt.transcribe(pcm, 16000)
                result['stt'] = {'status': 'verified', 'worker_pid': stt.worker_pid,
                                 'text': answer.text, 'device': config.device + '/' + config.compute_type}
            except Exception as error:
                result['stt'] = {'status': 'unavailable', 'reason': type(error).__name__,
                                 'reason_code': getattr(error, 'reason_code', 'model_error')}
                raise  # An explicitly requested STT gate must not report overall success.
            finally:
                stt.close()
                assert not stt.worker_alive
        result['active_spawn_children'] = len(multiprocessing.active_children())
        assert not multiprocessing.active_children()
        result['status'] = 'passed'
    except Exception as error:
        result['status'] = 'failed'
        result['error_type'] = type(error).__name__
        import traceback
        (root / 'tmp' / 'check-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
    finally:
        if client: client.close()
        if server: server.close()
        if admin: admin.close()
        if text: text.close()
        if h: h.close()
        if window: window.close()
        result['remaining_worker_threads'] = [t.name for t in threading.enumerate() if t.name.startswith('jarvis-')]
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
