from jarvis.config import JarvisConfig
from jarvis.doctor import JarvisDoctor
from jarvis.memory.in_memory_store import InMemoryMemoryStore
from jarvis.runtime.resource_manager import ResourceManager


def _doctor(config: JarvisConfig) -> JarvisDoctor:
    return JarvisDoctor(
        config,
        ResourceManager(),
        InMemoryMemoryStore(),
    )


def test_doctor_reports_system_default_microphone_as_valid(tmp_path) -> None:
    report = _doctor(
        JarvisConfig(
            microphone_device_index=None,
            settings_path=tmp_path / "missing.json",
        )
    ).report()

    assert "Microphone config..... SYSTEM DEFAULT" in report
    assert "Microphone config..... NOT CONFIGURED" not in report


def test_doctor_reports_explicit_microphone_device(tmp_path) -> None:
    report = _doctor(
        JarvisConfig(
            microphone_device_index=0,
            settings_path=tmp_path / "missing.json",
        )
    ).report()

    assert "Microphone config..... OK (device 0)" in report
