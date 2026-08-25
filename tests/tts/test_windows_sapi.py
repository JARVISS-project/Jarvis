from threading import enumerate as enumerate_threads
from threading import Event, Thread, get_ident
from typing import Any

from jarvis.tts.text import prepare_text_for_speech
from jarvis.tts.windows_sapi import WindowsSapiTTS


class FakeEngine:
    def __init__(self, run_error: Exception | None = None) -> None:
        self.properties: list[tuple[str, Any]] = []
        self.spoken: list[str] = []
        self.run_calls = 0
        self.stop_calls = 0
        self.run_error = run_error
        self.say_threads: list[int] = []
        self.run_threads: list[int] = []
        self.stop_threads: list[int] = []

    def setProperty(self, name: str, value: Any) -> None:
        self.properties.append((name, value))

    def say(self, text: str) -> None:
        self.spoken.append(text)
        self.say_threads.append(get_ident())

    def runAndWait(self) -> None:
        self.run_calls += 1
        self.run_threads.append(get_ident())
        if self.run_error is not None:
            raise self.run_error

    def stop(self) -> None:
        self.stop_calls += 1
        self.stop_threads.append(get_ident())


class FakeDriverProxy:
    def __init__(self) -> None:
        self.busy = True

    def setBusy(self, busy: bool) -> None:
        self.busy = busy


class FakePyttsxLifecycleEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.proxy = FakeDriverProxy()
        self.audible: list[str] = []

    def say(self, text: str) -> None:
        super().say(text)
        if self.proxy.busy:
            self.audible.append(text)

    def runAndWait(self) -> None:
        super().runAndWait()
        self.proxy.setBusy(False)


def test_sapi_engine_is_lazy_and_empty_text_does_not_initialize_it() -> None:
    factory_calls: list[bool] = []
    tts = WindowsSapiTTS(
        engine_factory=lambda: factory_calls.append(True) or FakeEngine()
    )

    tts.speak("   ")

    assert tts.is_engine_initialized is False
    assert factory_calls == []
    assert tts.is_worker_running is False


def test_sapi_configures_engine_and_speaks() -> None:
    engine = FakeEngine()
    tts = WindowsSapiTTS(
        rate=170,
        volume=0.8,
        voice_id="voice-id",
        engine_factory=lambda: engine,
    )

    tts.speak("Olá")

    assert engine.properties == [
        ("rate", 170),
        ("volume", 0.8),
        ("voice", "voice-id"),
    ]
    assert engine.spoken == ["Olá"]
    assert engine.run_calls == 1
    assert engine.say_threads == [tts.worker_ident]
    assert engine.run_threads == [tts.worker_ident]
    assert tts.worker_ident != get_ident()
    tts.close()


def test_engine_lifecycle_stays_inside_dedicated_worker() -> None:
    engine = FakeEngine()
    creation_threads: list[int] = []

    def factory() -> FakeEngine:
        creation_threads.append(get_ident())
        return engine

    tts = WindowsSapiTTS(engine_factory=factory)
    tts.speak("Teste")
    worker_ident = tts.worker_ident

    tts.close()

    assert worker_ident is not None
    assert creation_threads == [worker_ident]
    assert engine.say_threads == [worker_ident]
    assert engine.run_threads == [worker_ident]
    assert engine.stop_threads == [worker_ident]
    assert all(
        thread.name != "jarvis-sapi5" or not thread.is_alive()
        for thread in enumerate_threads()
    )


def test_sapi_reuses_engine_between_three_phrases() -> None:
    engine = FakePyttsxLifecycleEngine()
    factory_calls = 0

    def factory() -> FakeEngine:
        nonlocal factory_calls
        factory_calls += 1
        return engine

    tts = WindowsSapiTTS(engine_factory=factory)

    tts.speak("Primeira")
    tts.speak("Segunda")
    tts.speak("Terceira")

    assert factory_calls == 1
    assert engine.spoken == ["Primeira", "Segunda", "Terceira"]
    assert engine.audible == ["Primeira", "Segunda", "Terceira"]
    assert engine.run_calls == 3
    assert len(set(engine.say_threads)) == 1
    assert engine.say_threads == engine.run_threads
    assert tts.is_worker_running is True
    tts.close()


def test_second_speak_waits_for_its_own_run_and_wait_completion() -> None:
    second_started = Event()
    release_second = Event()

    class BlockingSecondEngine(FakeEngine):
        def runAndWait(self) -> None:
            super().runAndWait()
            if self.run_calls == 2:
                second_started.set()
                release_second.wait()

    engine = BlockingSecondEngine()
    tts = WindowsSapiTTS(engine_factory=lambda: engine)
    tts.speak("Primeira")
    caller = Thread(target=lambda: tts.speak("Segunda"))

    caller.start()
    try:
        assert second_started.wait(timeout=1.0) is True
        assert caller.is_alive() is True
    finally:
        release_second.set()
        caller.join(timeout=1.0)
        tts.close()

    assert caller.is_alive() is False
    assert engine.spoken == ["Primeira", "Segunda"]
    assert engine.run_calls == 2


def test_run_failure_discards_engine_and_allows_recovery() -> None:
    failed = FakeEngine(RuntimeError("driver ocupado"))
    recovered = FakeEngine()
    engines = iter([failed, recovered])
    tts = WindowsSapiTTS(engine_factory=lambda: next(engines))

    try:
        tts.speak("Primeira tentativa")
    except RuntimeError:
        pass
    tts.speak("Segunda tentativa")

    assert failed.stop_calls == 1
    assert recovered.spoken == ["Segunda tentativa"]
    assert tts.is_engine_initialized is True
    assert failed.say_threads == recovered.say_threads
    tts.close()


def test_close_releases_initialized_engine() -> None:
    engine = FakeEngine()
    tts = WindowsSapiTTS(engine_factory=lambda: engine)
    tts.speak("Olá")

    tts.close()
    tts.close()

    assert engine.stop_calls == 1
    assert tts.is_engine_initialized is False
    assert tts.is_worker_running is False


def test_stop_preserves_worker_and_later_speak_still_works() -> None:
    engine = FakePyttsxLifecycleEngine()
    tts = WindowsSapiTTS(engine_factory=lambda: engine)
    tts.speak("Primeira")
    tts.stop()
    assert engine.stop_calls == 1
    assert tts.is_worker_running is True
    tts.speak("Segunda")
    assert engine.spoken == ["Primeira", "Segunda"]
    assert engine.audible == ["Primeira", "Segunda"]
    tts.close()


def test_stop_interrupts_iterative_engine_on_worker_and_allows_reuse() -> None:
    started = Event()

    class InterruptibleEngine(FakeEngine):
        def __init__(self) -> None:
            super().__init__()
            self.busy = False
            self.end_calls = 0

        def say(self, text: str) -> None:
            super().say(text)
            self.busy = True

        def startLoop(self, use_driver_loop: bool) -> None:
            assert use_driver_loop is False
            started.set()

        def iterate(self) -> None:
            pass

        def isBusy(self) -> bool:
            return self.busy

        def stop(self) -> None:
            super().stop()
            self.busy = False

        def endLoop(self) -> None:
            self.end_calls += 1

    engine = InterruptibleEngine()
    tts = WindowsSapiTTS(engine_factory=lambda: engine)
    caller = Thread(target=lambda: tts.speak("fala longa"))
    caller.start()
    assert started.wait(timeout=1)
    tts.stop()
    caller.join(timeout=1)
    assert not caller.is_alive()
    assert engine.stop_threads == [tts.worker_ident]
    engine.busy = False
    started.clear()
    second = Thread(target=lambda: tts.speak("nova fala"))
    second.start()
    assert started.wait(timeout=1)
    tts.stop()
    second.join(timeout=1)
    assert not second.is_alive()
    assert engine.spoken == ["fala longa", "nova fala"]
    tts.close()


def test_speak_async_returns_playing_handle() -> None:
    release = Event()

    class BlockingEngine(FakeEngine):
        def runAndWait(self) -> None:
            release.wait()

    tts = WindowsSapiTTS(engine_factory=lambda: BlockingEngine())
    playback = tts.speak_async("assíncrono")
    assert playback.is_playing
    release.set()
    assert playback.wait(timeout=1)
    assert not playback.is_playing
    tts.close()


def test_basic_markdown_is_prepared_without_changing_content() -> None:
    text = "**Arquivos:**\n- README.md\n- [Projeto](https://example.test)"

    assert prepare_text_for_speech(text) == (
        "Arquivos:\nREADME.md\nProjeto"
    )


def test_speech_text_removes_emoji_and_preserves_portuguese_accents() -> None:
    assert prepare_text_for_speech(
        "Olá! 😊 **Tudo bem?** Ação concluída."
    ) == "Olá! Tudo bem? Ação concluída."
