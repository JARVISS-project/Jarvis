import pytest
import os
from unittest import mock
from pathlib import Path
from jarvis.audio.devices import list_input_devices, resolve_microphone_index, probe_microphone, AudioDevice
from jarvis.config import JarvisConfig

def test_list_input_devices(monkeypatch):
    class FakePvRecorder:
        @staticmethod
        def get_available_devices():
            return ["Mic A", "Mic B", "Mic A", "Mic C"]

    # Mock import in function
    import sys
    pvrecorder_mock = mock.MagicMock()
    pvrecorder_mock.PvRecorder = FakePvRecorder
    monkeypatch.setitem(sys.modules, "pvrecorder", pvrecorder_mock)

    devices = list_input_devices()
    assert len(devices) == 4
    assert devices[0].name == "Mic A (1/2)"
    assert devices[0].index == 0
    assert devices[0].is_default is False
    assert devices[1].name == "Mic B"
    assert devices[2].name == "Mic A (2/2)"
    assert devices[2].index == 2
    assert devices[3].name == "Mic C"

def test_resolve_microphone_index(monkeypatch):
    class FakePvRecorder:
        @staticmethod
        def get_available_devices():
            return ["System Mic", "USB Mic"]

    pvrecorder_mock = mock.MagicMock()
    pvrecorder_mock.PvRecorder = FakePvRecorder
    import sys
    monkeypatch.setitem(sys.modules, "pvrecorder", pvrecorder_mock)

    # Explicit override takes precedence
    assert resolve_microphone_index("System Mic", explicit_index=99) == 99

    # Default when None
    assert resolve_microphone_index(None) == -1

    # Name match
    assert resolve_microphone_index("USB Mic") == 1

    # Missing device falls back to default
    assert resolve_microphone_index("Nonexistent") == -1

def test_config_persistence(monkeypatch):
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(b'{"microphone_device_name": "Saved Mic"}')
        settings_file_path = f.name

    try:
        settings_file = Path(settings_file_path)
        config = JarvisConfig(settings_path=settings_file)
        assert config.microphone_device_name == "Saved Mic"

        # Save a new one
        config.save_microphone_settings("New Mic")
        assert config.microphone_device_name == "New Mic"
        assert '"microphone_device_name": "New Mic"' in settings_file.read_text(encoding="utf-8")

        # Clear it
        config.save_microphone_settings(None)
        assert config.microphone_device_name is None
        assert '"microphone_device_name"' not in settings_file.read_text(encoding="utf-8")
    finally:
        os.remove(settings_file_path)

def test_probe_microphone_success(monkeypatch):
    class FakeRecorderInstance:
        def __init__(self, device_index, frame_length):
            self.device_index = device_index
        def start(self):
            pass
        def read(self):
            return [0] * 512
        def stop(self):
            pass
        def delete(self):
            pass

    class FakePvRecorder:
        def __new__(cls, *args, **kwargs):
            return FakeRecorderInstance(*args, **kwargs)

    pvrecorder_mock = mock.MagicMock()
    pvrecorder_mock.PvRecorder = FakePvRecorder
    import sys
    monkeypatch.setitem(sys.modules, "pvrecorder", pvrecorder_mock)

    assert probe_microphone(0, frames_to_read=2) is True

def test_probe_microphone_failure(monkeypatch):
    class FakeRecorderInstance:
        def __init__(self, device_index, frame_length):
            pass
        def start(self):
            raise Exception("Device busy")
        def stop(self):
            pass
        def delete(self):
            pass

    class FakePvRecorder:
        def __new__(cls, *args, **kwargs):
            return FakeRecorderInstance(*args, **kwargs)

    pvrecorder_mock = mock.MagicMock()
    pvrecorder_mock.PvRecorder = FakePvRecorder
    import sys
    monkeypatch.setitem(sys.modules, "pvrecorder", pvrecorder_mock)

    assert probe_microphone(0, frames_to_read=2) is False


def test_duplicate_names_do_not_collide_with_real_suffixed_names(monkeypatch):
    import sys
    module = mock.MagicMock()
    module.PvRecorder.get_available_devices.return_value = [
        "Mic", "Mic", "Mic (1/2)", "Mic (1/2) #",
    ]
    monkeypatch.setitem(sys.modules, "pvrecorder", module)
    names = [device.name for device in list_input_devices()]
    assert len(set(names)) == len(names)
    assert names == [device.name for device in list_input_devices()]
    assert resolve_microphone_index(names[1]) == 1


def test_missing_saved_device_warns_and_never_selects_another(monkeypatch, tmp_path):
    from jarvis.audio import devices
    from tests.test_doctor_microphone import _doctor
    monkeypatch.setattr(devices, "list_input_devices", lambda: [AudioDevice(0, "Other")])
    warnings = []
    assert resolve_microphone_index("Saved", warning_callback=warnings.append) == -1
    assert "indisponível" in warnings[0]
    config = JarvisConfig(microphone_device_name="Saved", settings_path=tmp_path / "s.json")
    assert "indisponível" in _doctor(config).report()
    assert resolve_microphone_index("Saved", 7, warnings.append) == 7
    assert len(warnings) == 1


def test_probe_read_error_still_stops_and_deletes(monkeypatch):
    import sys
    module = mock.MagicMock()
    recorder = module.PvRecorder.return_value
    recorder.read.side_effect = OSError("read failed")
    recorder.stop.side_effect = OSError("stop failed")
    monkeypatch.setitem(sys.modules, "pvrecorder", module)
    assert not probe_microphone(0)
    recorder.stop.assert_called_once()
    recorder.delete.assert_called_once()


def test_environment_index_overrides_saved_name(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_MICROPHONE_INDEX", "3")
    config = JarvisConfig(microphone_device_name="Saved", settings_path=tmp_path / "s.json")
    assert resolve_microphone_index(config.microphone_device_name, config.microphone_device_index) == 3
