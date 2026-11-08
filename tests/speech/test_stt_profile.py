import json
from pathlib import Path
import subprocess
import threading
import time

import pytest

from jarvis.config import JarvisConfig
from jarvis.speech.faster_whisper import FasterWhisperSTT, SttLoadState
from jarvis.speech.gpu_budget import GpuMemory, query_gpu_memory, require_free_vram
from jarvis.speech.profile import parse_stt_profile


class FakeModel:
    def transcribe(self, _audio: object, **_kwargs: object) -> tuple[list, object]:
        return [], object()


def test_profiles_are_explicit_and_unknown_value_fails() -> None:
    assert parse_stt_profile("medium").model == "medium"
    promoted = parse_stt_profile("large-v3")
    assert (promoted.model, promoted.device, promoted.compute_type) == (
        "large-v3", "cuda", "float16",
    )
    assert promoted.allow_device_fallback is False
    with pytest.raises(ValueError, match="Perfil STT inválido"):
        parse_stt_profile("inventado")


def test_config_promotion_backup_atomic_write_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"agent_name":"Jarvis"}\n', encoding="utf-8")
    backup = tmp_path / "private" / "settings.before-23al.json"
    replacements: list[tuple[Path, Path]] = []
    original_replace = __import__("os").replace

    def observed_replace(source: Path, target: Path) -> None:
        replacements.append((Path(source), Path(target)))
        original_replace(source, target)

    monkeypatch.setattr("jarvis.config.os.replace", observed_replace)
    config = JarvisConfig(settings_path=settings)
    assert config.stt_profile == "medium"
    config.save_stt_profile(
        "large-v3", execution_mode="process", backup_path=backup,
    )
    assert json.loads(backup.read_text(encoding="utf-8")) == {
        "agent_name": "Jarvis"
    }
    promoted = JarvisConfig(settings_path=settings)
    assert (promoted.stt_profile, promoted.stt_execution_mode) == (
        "large-v3", "process",
    )
    assert replacements and replacements[-1][1] == settings
    JarvisConfig(settings_path=settings).save_stt_profile(
        "medium", execution_mode="process",
    )
    rolled_back = JarvisConfig(settings_path=settings)
    assert (rolled_back.stt_profile, rolled_back.stt_execution_mode) == (
        "medium", "process",
    )
    rolled_back.save_stt_profile("medium", execution_mode="legacy")
    assert JarvisConfig(settings_path=settings).stt_execution_mode == "legacy"
    assert not list(tmp_path.glob(".*.tmp"))


def test_invalid_configured_profile_fails_clearly(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"stt_profile":"cpu-magic"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Perfil STT inválido"):
        JarvisConfig(settings_path=settings)


def test_semantic_read_flag_defaults_off_and_round_trips_atomically(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"agent_name":"Jarvis"}', encoding="utf-8")
    backup = tmp_path / "private" / "before-23an.json"
    config = JarvisConfig(settings_path=settings)
    assert config.semantic_read_tools_enabled is False
    config.save_semantic_read_tools_enabled(True, backup_path=backup)
    assert JarvisConfig(settings_path=settings).semantic_read_tools_enabled is True
    JarvisConfig(settings_path=settings).save_semantic_read_tools_enabled(False)
    assert JarvisConfig(settings_path=settings).semantic_read_tools_enabled is False
    assert json.loads(backup.read_text(encoding="utf-8")) == {
        "agent_name": "Jarvis"
    }
    with pytest.raises(TypeError):
        config.save_semantic_read_tools_enabled("yes")  # type: ignore[arg-type]

    settings.write_text(
        '{"stt_profile":"medium","stt_execution_mode":"magic"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Modo de execução STT inválido"):
        JarvisConfig(settings_path=settings)


def test_gpu_budget_parsing_and_insufficient_margin() -> None:
    def runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, "12288, 5228, 7060\n", "")

    assert query_gpu_memory(runner) == GpuMemory(12288, 5228, 7060)
    require_free_vram(5781, GpuMemory(12288, 5228, 7060))
    with pytest.raises(RuntimeError, match="VRAM insuficiente"):
        require_free_vram(5781, GpuMemory(12288, 8000, 4288))


def test_preload_ready_reports_effective_options_without_cpu_fallback() -> None:
    calls: list[tuple[str, str, str]] = []
    output: list[str] = []

    def factory(model: str, *, device: str, compute_type: str) -> FakeModel:
        calls.append((model, device, compute_type))
        return FakeModel()

    stt = FasterWhisperSTT(
        model_name="large-v3", device="cuda", compute_type="float16",
        allow_device_fallback=False, model_factory=factory, output=output.append,
    )
    assert stt.preload(.5)
    assert stt.load_state is SttLoadState.READY
    assert calls == [("large-v3", "cuda", "float16")]
    assert "device=cuda compute_type=float16" in output[-1]
    stt.close()
    assert stt.load_state is SttLoadState.STOPPED
    assert not stt.is_model_loaded


def test_preload_timeout_failure_and_shutdown_during_load() -> None:
    release = threading.Event()

    def factory(*_args: object, **_kwargs: object) -> FakeModel:
        release.wait(1.0)
        return FakeModel()

    stt = FasterWhisperSTT(model_factory=factory)
    assert not stt.preload(.05)
    assert stt.load_state is SttLoadState.FAILED
    assert stt.load_error == "load_timeout"
    stt.close()
    release.set()
    deadline = time.monotonic() + 1.0
    while stt._preload_thread is not None and stt._preload_thread.is_alive():
        assert time.monotonic() < deadline
        time.sleep(.005)
    assert stt.load_state is SttLoadState.STOPPED
    assert not stt.is_model_loaded


def test_model_failure_is_explicit_and_never_falls_back_to_cpu() -> None:
    calls: list[tuple[str, str, str]] = []

    def factory(model: str, *, device: str, compute_type: str) -> FakeModel:
        calls.append((model, device, compute_type))
        raise RuntimeError("CUDA unavailable")

    stt = FasterWhisperSTT(
        model_name="large-v3", device="cuda", compute_type="float16",
        allow_device_fallback=False, model_factory=factory,
    )
    assert not stt.preload(.5)
    assert stt.load_state is SttLoadState.FAILED
    assert stt.load_error == "RuntimeError"
    assert calls == [("large-v3", "cuda", "float16")]
