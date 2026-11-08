from __future__ import annotations

import json
from pathlib import Path

from jarvis.config import JarvisConfig
from jarvis.runtime import diagnostic
from jarvis.speech.gpu_budget import GpuBudgetManager, GpuMemory


def _snapshot(root: Path) -> set[tuple[str, int]]:
    return {(str(path.relative_to(root)), path.stat().st_size)
            for path in root.rglob("*") if path.is_file()}


def test_diagnostic_is_read_only_and_does_not_initialize_effects(
    tmp_path: Path, monkeypatch,
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"stt_profile": "large-v3",
                                    "stt_execution_mode": "process"}), encoding="utf-8")
    cfg = JarvisConfig(settings_path=settings, filesystem_base_directory=tmp_path)
    before = _snapshot(tmp_path)
    for module_name, attribute in (
        ("jarvis.speech.stt_process", "SttProcessClient"),
        ("jarvis.speech.faster_whisper", "FasterWhisperSTT"),
        ("jarvis.tools.executor", "ToolExecutor"),
    ):
        try:
            module = __import__(module_name, fromlist=[attribute])
        except ImportError:
            continue
        monkeypatch.setattr(module, attribute, None)

    result = diagnostic.run_diagnostics(
        cfg, GpuBudgetManager(lambda: GpuMemory(12288, 2000, 10288))
    )

    assert _snapshot(tmp_path) == before
    assert result["stt"]["model"] == "large-v3"
    assert result["stt"]["execution_mode"] == "process"
    assert result["stt"]["session_active"] is False
    assert result["tts"]["session_active"] is False
    assert result["microphone"]["session_active"] is False


def test_every_component_has_a_structured_allowed_status(monkeypatch) -> None:
    monkeypatch.setattr(diagnostic, "check_gpu", lambda: {"status": "unavailable"})
    monkeypatch.setattr(diagnostic, "check_ollama", lambda: {"status": "unavailable"})
    cfg = JarvisConfig(settings_path=Path("does-not-exist.json"))
    output = diagnostic.run_diagnostics(
        cfg, GpuBudgetManager(lambda: GpuMemory(12288, 2000, 10288))
    )
    allowed = {"ready", "disabled", "unavailable", "degraded", "error"}
    for name in ("python", "config", "gpu", "stt", "ollama", "microphone",
                 "tts", "browser", "gsmtc", "core_audio"):
        assert output[name]["status"] in allowed
    assert set(output["flags"]) == {
        "semantic_read_tools_enabled", "local_control_enabled",
        "gsmtc_enabled", "safe_browser_enabled",
    }


def test_optional_dependencies_are_not_imported_at_module_import() -> None:
    source = Path(diagnostic.__file__).read_text(encoding="utf-8")
    for forbidden in ("import torch", "import pyttsx3", "import sounddevice",
                      "SttCandidateShadow"):
        assert forbidden not in source


def test_human_report_explains_vram_shortfall_without_blame() -> None:
    report = {
        "config": {"status": "ready"},
        "gpu": {"status": "ready", "process_memory_attribution": "unavailable_wddm"},
        "stt": {"status": "degraded", "model": "large-v3", "device": "cuda",
                "compute_type": "float16", "execution_mode": "process",
                "vram_budget": {"total_mib": 12288, "free_mib": 5233,
                                "required_mib": 5781}},
        "microphone": {"status": "ready"}, "tts": {"status": "ready"},
        "ollama": {"status": "ready"}, "browser": {"status": "ready"},
        "gsmtc": {"status": "unavailable"}, "core_audio": {"status": "unavailable"},
        "apps": {"spotify": {"status": "ready"}, "obsidian": {"status": "ready"}},
        "capabilities": {"enabled": ["safe_browser"], "disabled": []},
    }
    rendered = diagnostic.render_diagnostic_report(report)
    assert "faltam=548 MiB" in rendered
    assert "Nenhum processo será encerrado" in rendered
    assert "WDDM não informa consumo confiável" in rendered
    assert "jarvis.bat --doctor" in rendered
    assert diagnostic.diagnostic_exit_code(report) == 1
