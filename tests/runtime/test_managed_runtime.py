from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from threading import Event

import pytest

from jarvis import __main__ as main_module
from jarvis.config import JarvisConfig
from jarvis.runtime import cli


class FakeGuard:
    instance_id = "instance"

    def __init__(self, _path: Path) -> None:
        self.events: list[str] = []

    def acquire(self) -> None:
        self.events.append("acquire")

    def release(self) -> None:
        self.events.append("release")


class FakeHealthStore:
    instances: list["FakeHealthStore"] = []

    def __init__(self, _path: Path) -> None:
        self.publications: list[dict[str, object]] = []
        self.cleared = 0
        self.__class__.instances.append(self)

    def publish(self, **kwargs):
        self.publications.append(kwargs)

    def stop_requested(self, _instance_id: str) -> bool:
        return True

    def clear_stop_request(self) -> None:
        self.cleared += 1


class FakeHarness:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def runtime_args(*arguments: str):
    return cli.argument_parser().parse_args(list(arguments))


@pytest.mark.parametrize('voice', [False, True])
@pytest.mark.parametrize('fail_ui', [False, True])
def test_text_ui_opens_center_without_voice_and_preserves_cleanup(
    monkeypatch, tmp_path, runtime_fakes, voice, fail_ui,
):
    from tests.integration.test_administration_23bh import build
    from tests.voice.test_pipeline import FakeTextToSpeech

    harness, service = build(tmp_path)
    config = service.config
    config.ambient_ui_endpoint_path = tmp_path / 'ui.json'
    monkeypatch.setattr(cli, '_runtime_paths', lambda: (
        tmp_path / 'instance.json', tmp_path / 'status.json', tmp_path / 'runtime.log'))
    monkeypatch.setattr(main_module, 'build_harness', lambda *a, **k: harness)
    launches, stopped, voice_builds, closed = [], [], [], []
    real_close = harness.close

    def close():
        closed.append(True)
        real_close()

    monkeypatch.setattr(harness, 'close', close)

    def build_pipeline(*, harness_builder, **kwargs):
        assert voice, 'Text launch must never build the microphone/voice pipeline'
        voice_builds.append(True)
        harness_builder(None, None)
        return SimpleNamespace(text_to_speech=FakeTextToSpeech(), _stop_requested=Event(),
                               run=lambda: None, request_stop=lambda: None,
                               handle_ui_command=lambda command: None)

    def launch(endpoint, **kwargs):
        launches.append((endpoint, kwargs))
        assert endpoint.is_file()  # IPC must be ready before the child starts.
        if fail_ui:
            raise OSError('synthetic UI launch failure')
        return sentinel

    sentinel = object()
    monkeypatch.setattr(main_module, 'build_voice_pipeline', build_pipeline)
    monkeypatch.setattr(cli, 'launch_ambient_ui', launch)
    monkeypatch.setattr(cli, 'stop_spawned_ambient_ui', stopped.append)
    args = runtime_args('--ui', *(['--voice'] if voice else []))
    try:
        with pytest.raises(OSError, match='synthetic UI') if fail_ui else nullcontext():
            cli._run_managed_runtime(args, config)
        assert launches == [(config.ambient_ui_endpoint_path, {'open_control_center': not voice})]
        assert bool(voice_builds) is voice
        assert closed == [True]
        assert stopped == ([] if fail_ui else [sentinel])
        assert not config.ambient_ui_endpoint_path.exists()
        assert runtime_fakes[0].events == ['acquire', 'release']
    finally:
        real_close()


@pytest.fixture
def runtime_fakes(monkeypatch: pytest.MonkeyPatch):
    guards: list[FakeGuard] = []
    FakeHealthStore.instances.clear()

    def guard_factory(path: Path) -> FakeGuard:
        guard = FakeGuard(path)
        guards.append(guard)
        return guard

    monkeypatch.setattr(cli, "SingleInstanceGuard", guard_factory)
    monkeypatch.setattr(cli, "RuntimeHealthStore", FakeHealthStore)
    monkeypatch.setattr(cli, "_install_signal_handlers", lambda _callback: {})
    monkeypatch.setattr(cli, "_restore_signal_handlers", lambda _previous: None)
    monkeypatch.setattr(
        main_module,
        "_start_desktop_kill_switch",
        lambda _config: (object(), None),
    )
    return guards


def test_core_only_background_starts_and_cleans_once(
    monkeypatch: pytest.MonkeyPatch,
    runtime_fakes,
) -> None:
    harness = FakeHarness()
    builds: list[object] = []

    def build(*_args, **_kwargs):
        builds.append(object())
        return harness

    monkeypatch.setattr(main_module, "build_harness", build)

    cli._run_managed_runtime(runtime_args("--background-child"), JarvisConfig())

    assert len(builds) == 1
    assert harness.closed == 1
    assert runtime_fakes[0].events == ["acquire", "release"]
    assert FakeHealthStore.instances[0].cleared == 1


def test_partial_remote_startup_rolls_back_harness_and_lock(
    monkeypatch: pytest.MonkeyPatch,
    runtime_fakes,
) -> None:
    harness = FakeHarness()
    monkeypatch.setattr(main_module, "build_harness", lambda *_args, **_kwargs: harness)
    monkeypatch.setattr(
        cli,
        "_build_gateway",
        lambda *_args: (_ for _ in ()).throw(OSError("bind failed")),
    )

    with pytest.raises(OSError, match="bind failed"):
        cli._run_managed_runtime(
            runtime_args("--background-child", "--remote"),
            JarvisConfig(),
        )

    assert harness.closed == 1
    assert runtime_fakes[0].events == ["acquire", "release"]
    assert FakeHealthStore.instances[0].publications[-1]["last_startup_error"] == "OSError"


def test_remote_and_voice_share_one_harness(
    monkeypatch: pytest.MonkeyPatch,
    runtime_fakes,
) -> None:
    harness = FakeHarness()
    builds: list[FakeHarness] = []
    gateway_harnesses: list[FakeHarness] = []

    def build(*_args, **_kwargs):
        builds.append(harness)
        return harness

    class FakePipeline:
        def run(self) -> None:
            return

        def close(self) -> None:
            return

        def request_stop(self) -> None:
            return

    def build_pipeline(*, harness_builder, **_kwargs):
        assert harness_builder(None, None) is harness
        return FakePipeline()

    class FakeGateway:
        def start(self) -> None:
            return

        def stop(self) -> None:
            return

    def build_gateway(_args, _config, current, *_rest):
        gateway_harnesses.append(current)
        return FakeGateway()

    monkeypatch.setattr(main_module, "build_harness", build)
    monkeypatch.setattr(main_module, "build_voice_pipeline", build_pipeline)
    monkeypatch.setattr(cli, "_build_gateway", build_gateway)

    cli._run_managed_runtime(
        runtime_args("--background-child", "--voice", "--remote"),
        JarvisConfig(),
    )

    assert builds == [harness]
    assert gateway_harnesses == [harness]
    assert harness.closed == 1
