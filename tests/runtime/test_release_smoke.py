from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from jarvis.config import JarvisConfig
from jarvis.runtime import cli, diagnostic
from jarvis.runtime.instance import InstanceAlreadyRunningError, SingleInstanceGuard
from jarvis.speech.gpu_budget import (
    GpuBudgetDecision, GpuBudgetError, GpuBudgetManager, GpuMemory,
)


def _safe_checks(monkeypatch) -> None:
    monkeypatch.setattr(diagnostic, "check_gpu", lambda: {"status": "ready"})
    monkeypatch.setattr(diagnostic, "check_ollama", lambda: {"status": "ready"})
    monkeypatch.setattr(diagnostic, "check_browser", lambda: {"status": "ready"})
    monkeypatch.setattr(diagnostic, "check_apps", lambda: {
        "spotify": {"status": "ready"}, "obsidian": {"status": "ready"},
    })


@pytest.mark.parametrize(("free_mib", "expected"), [
    (7000, "ready"), (5000, "degraded"),
])
def test_doctor_budget_states_are_fake_and_effect_free(
    monkeypatch, tmp_path: Path, free_mib: int, expected: str,
) -> None:
    _safe_checks(monkeypatch)
    config = JarvisConfig(settings_path=tmp_path / "missing.json")
    config.stt_profile = "large-v3"
    config.stt_execution_mode = "process"
    report = diagnostic.run_diagnostics(
        config,
        GpuBudgetManager(lambda: GpuMemory(12288, 12288 - free_mib, free_mib)),
    )
    assert report["stt"]["status"] == expected
    assert report["stt"]["session_active"] is False
    assert report["microphone"]["session_active"] is False
    assert report["tts"]["session_active"] is False


def test_doctor_handles_missing_gpu_and_invalid_configuration(
    monkeypatch, tmp_path: Path,
) -> None:
    _safe_checks(monkeypatch)
    settings = tmp_path / "settings.json"
    settings.write_text("{invalid", encoding="utf-8")
    report = diagnostic.run_diagnostics(
        JarvisConfig(settings_path=settings),
        GpuBudgetManager(lambda: (_ for _ in ()).throw(RuntimeError("missing"))),
    )
    assert report["config"]["status"] == "degraded"
    assert report["stt"]["vram_budget"]["status"] == "unavailable"
    assert diagnostic.diagnostic_exit_code(report) == 1


def test_official_doctor_cli_reuses_runtime_diagnostic(monkeypatch, capsys) -> None:
    report = {"config": {"status": "ready"}, "stt": {"status": "ready"}}
    monkeypatch.setattr(diagnostic, "run_diagnostics", lambda _config: report)
    monkeypatch.setattr(diagnostic, "render_diagnostic_report", lambda _report: "OK")
    monkeypatch.setattr(diagnostic, "diagnostic_exit_code", lambda _report: 0)
    with pytest.raises(SystemExit) as exited:
        cli.main(["--doctor"])
    assert exited.value.code == 0
    assert capsys.readouterr().out.strip() == "OK"


def test_vram_startup_error_is_actionable_and_never_starts_runtime(monkeypatch) -> None:
    decision = GpuBudgetDecision(
        "insufficient", 5781, GpuMemory(12288, 7000, 5288),
    )
    monkeypatch.setattr(
        cli, "_run_managed_runtime",
        lambda _args, _config: (_ for _ in ()).throw(GpuBudgetError(decision)),
    )
    with pytest.raises(SystemExit) as exited:
        cli.main(["--voice"])
    message = str(exited.value)
    assert "faltam=493 MiB" in message
    assert "Nenhum processo foi encerrado" in message
    assert "jarvis.bat --doctor" in message


def test_second_instance_and_stale_recovery_leave_no_lock(tmp_path: Path) -> None:
    metadata = tmp_path / "instance.json"
    first = SingleInstanceGuard(metadata, platform="linux", pid=101)
    second = SingleInstanceGuard(
        metadata, platform="linux", pid=202,
        is_process_running=lambda pid: pid == 101,
    )
    with first:
        with pytest.raises(InstanceAlreadyRunningError):
            second.acquire()
    assert not metadata.exists()
    recovered = SingleInstanceGuard(
        metadata, platform="linux", pid=303,
        is_process_running=lambda _pid: False,
    )
    with recovered:
        assert metadata.exists()
    assert not metadata.exists()


def test_doctor_leaves_zero_worker_residual(monkeypatch, tmp_path: Path) -> None:
    before = {child.pid for child in multiprocessing.active_children()}
    _safe_checks(monkeypatch)
    report = diagnostic.run_diagnostics(
        JarvisConfig(settings_path=tmp_path / "missing.json"),
        GpuBudgetManager(lambda: GpuMemory(12288, 4000, 8288)),
    )
    after = {child.pid for child in multiprocessing.active_children()}
    assert report["stt"]["session_active"] is False
    assert after == before
