from pathlib import Path

from jarvis.config import JarvisConfig
from jarvis.integrations.google.models import ConnectionState
from jarvis.runtime import daily_driver


def test_daily_driver_report_distinguishes_ready_config_and_login(
    monkeypatch,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    model = tmp_path / "voice.onnx"
    config_file = tmp_path / "voice.onnx.json"
    model.write_bytes(b"x")
    config_file.write_text("{}", encoding="utf-8")
    config = JarvisConfig(
        settings_path=tmp_path / "missing-settings.json",
        obsidian_vault_path=vault,
        obsidian_enabled=True,
        desktop_read_enabled=True,
        desktop_control_enabled=True,
        vision_enabled=False,
        browser_enabled=True,
        tts_provider="piper",
        tts_piper_model_path=model,
        tts_piper_config_path=config_file,
    )
    monkeypatch.setattr(daily_driver, "find_spec", lambda _name: object())
    monkeypatch.setattr(daily_driver, "_playwright_browser_ready", lambda: True)

    checks = daily_driver.build_daily_driver_report(
        config,
        startup_command=None,
        google_calendar_state=ConnectionState.DISCONNECTED,
        google_calendar_write_state=ConnectionState.DISCONNECTED,
    )
    by_name = {check.name: check for check in checks}

    assert by_name["audio"].status is daily_driver.Readiness.READY
    assert by_name["tts"].status is daily_driver.Readiness.READY
    assert by_name["obsidian"].status is daily_driver.Readiness.READY
    assert by_name["desktop"].status is daily_driver.Readiness.READY
    assert by_name["vision"].status is daily_driver.Readiness.CONFIG_REQUIRED
    assert by_name["startup"].status is daily_driver.Readiness.CONFIG_REQUIRED
    assert by_name["google_calendar_read"].status is daily_driver.Readiness.LOGIN_REQUIRED


def test_connected_google_and_startup_are_ready(monkeypatch, tmp_path: Path) -> None:
    config = JarvisConfig(
        settings_path=tmp_path / "settings.json",
        tts_provider="sapi",
    )
    monkeypatch.setattr(daily_driver, "find_spec", lambda _name: object())
    monkeypatch.setattr(daily_driver, "_playwright_browser_ready", lambda: True)

    checks = daily_driver.build_daily_driver_report(
        config,
        startup_command="pythonw -m jarvis --background-child",
        google_calendar_state=ConnectionState.CONNECTED,
        google_calendar_write_state=ConnectionState.CONNECTED,
    )
    by_name = {check.name: check for check in checks}

    assert by_name["startup"].status is daily_driver.Readiness.READY
    assert by_name["google_calendar_read"].status is daily_driver.Readiness.READY
    assert by_name["google_calendar_write"].status is daily_driver.Readiness.READY


def test_render_is_explicitly_read_only(monkeypatch, tmp_path: Path) -> None:
    config = JarvisConfig(settings_path=tmp_path / "settings.json")
    monkeypatch.setattr(daily_driver, "find_spec", lambda _name: object())
    checks = daily_driver.build_daily_driver_report(
        config,
        startup_command=None,
        google_calendar_state=ConnectionState.DISCONNECTED,
        google_calendar_write_state=ConnectionState.DISCONNECTED,
    )

    rendered = daily_driver.render_daily_driver_report(checks)

    assert "JARVIS Daily Driver Check" in rendered
    assert "LOGIN_REQUIRED" in rendered
    assert "somente leitura" in rendered


def test_browser_readiness_probe_runs_once(monkeypatch, tmp_path: Path) -> None:
    config = JarvisConfig(
        settings_path=tmp_path / "settings.json",
        browser_enabled=True,
    )
    calls = 0

    def ready() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(daily_driver, "_playwright_browser_ready", ready)

    checks = daily_driver.build_daily_driver_report(
        config,
        startup_command=None,
        google_calendar_state=ConnectionState.CONNECTED,
        google_calendar_write_state=ConnectionState.CONNECTED,
    )

    assert calls == 1
    by_name = {check.name: check for check in checks}
    assert by_name["browser"].status is daily_driver.Readiness.READY


def test_browser_probe_runs_in_isolated_subprocess(
    monkeypatch,
) -> None:
    monkeypatch.setattr(daily_driver, "find_spec", lambda _name: object())
    calls: list[tuple[list[str], dict[str, object]]] = []

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr(daily_driver.subprocess, "run", fake_run)

    assert daily_driver._playwright_browser_ready() is True
    command, kwargs = calls[0]
    assert command[0] == daily_driver.sys.executable
    assert command[1] == "-c"
    assert kwargs["stdout"] is daily_driver.subprocess.DEVNULL
    assert kwargs["stderr"] is daily_driver.subprocess.DEVNULL


def test_wake_detail_reports_baseline_when_verifier_is_absent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = JarvisConfig(
        settings_path=tmp_path / "settings.json",
        wake_word_verifier_path=tmp_path / "missing-verifier.npz",
    )
    monkeypatch.setattr(daily_driver, "find_spec", lambda _name: object())

    detail = daily_driver._wake_word_detail(config)

    assert "hey_jarvis_v0.1.onnx" in detail
    assert "verifier externo desligado" in detail
    assert "threshold efetivo=0.5" in detail
    assert str(config.wake_word_verifier_path) in detail


def test_wake_detail_reports_valid_local_verifier(
    monkeypatch,
    tmp_path: Path,
) -> None:
    verifier_path = tmp_path / "verifier.npz"
    verifier_path.write_bytes(b"numeric-placeholder")
    config = JarvisConfig(
        settings_path=tmp_path / "settings.json",
        wake_word_verifier_path=verifier_path,
        wake_word_verifier_enabled=True,
        wake_word_verifier_candidate_threshold=0.12,
        wake_word_verifier_threshold=0.70,
        wake_word_verifier_strong_threshold=0.85,
    )
    monkeypatch.setattr(daily_driver, "find_spec", lambda _name: object())

    from jarvis.activation import verifier as verifier_module

    monkeypatch.setattr(
        verifier_module,
        "load_safe_wake_verifier",
        lambda _path: type("Verifier", (), {"model_name": "hey_jarvis"})(),
    )

    detail = daily_driver._wake_word_detail(config)

    assert "verifier local" in detail
    assert "candidate=0.12" in detail
    assert "threshold efetivo=0.7" in detail
    assert "strong=0.85" in detail


def test_wake_detail_invalid_verifier_falls_back_to_baseline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    verifier_path = tmp_path / "broken.npz"
    verifier_path.write_bytes(b"broken")
    config = JarvisConfig(
        settings_path=tmp_path / "settings.json",
        wake_word_verifier_path=verifier_path,
        wake_word_verifier_enabled=True,
    )
    monkeypatch.setattr(daily_driver, "find_spec", lambda _name: object())

    from jarvis.activation import verifier as verifier_module

    def fail(_path):
        raise ValueError("invalid verifier")

    monkeypatch.setattr(verifier_module, "load_safe_wake_verifier", fail)

    detail = daily_driver._wake_word_detail(config)

    assert "verifier inválido -> baseline threshold=0.5" in detail
