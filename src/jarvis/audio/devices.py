"""PvRecorder names are preferences; numeric indices are resolved at startup."""

from collections import Counter
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    is_default: bool = False


def list_input_devices() -> list[AudioDevice]:
    try:
        from pvrecorder import PvRecorder

        names = [name.strip() for name in PvRecorder.get_available_devices()]
    except (ImportError, RuntimeError, OSError):
        return []
    totals = Counter(names)
    seen: Counter[str] = Counter()
    reserved = set(names)
    devices = []
    for index, name in enumerate(names):
        seen[name] += 1
        label = name
        if totals[name] > 1:
            label = f"{name} ({seen[name]}/{totals[name]})"
            while label in reserved:
                label += " #"
        reserved.add(label)
        # PvRecorder exposes no physical default identity; only -1 means default.
        devices.append(AudioDevice(index, label))
    return devices


def microphone_warning(device_name: str | None) -> str | None:
    if device_name and not any(d.name == device_name for d in list_input_devices()):
        return "Dispositivo configurado indisponível; usando padrão do sistema."
    return None


def resolve_microphone_index(
    device_name: str | None,
    explicit_index: int | None = None,
    warning_callback: Callable[[str], None] | None = None,
) -> int:
    if explicit_index is not None:
        return explicit_index
    if not device_name:
        return -1
    for device in list_input_devices():
        if device.name == device_name:
            return device.index
    if warning_callback is not None:
        warning_callback("Dispositivo configurado indisponível; usando padrão do sistema.")
    return -1


def probe_microphone(index: int, frame_length: int = 512, frames_to_read: int = 10, *, permission_check=None) -> bool:
    """Read bounded frames without storing audio and always dispose the stream."""
    if not 1 <= frames_to_read <= 32 or not 1 <= frame_length <= 4096:
        return False
    try:
        from pvrecorder import PvRecorder
    except ImportError:
        return False
    recorder = None
    success = False
    try:
        if permission_check is None:
            from jarvis.config import JarvisConfig
            from jarvis.security.permissions import PermissionStore
            policy = PermissionStore.for_config(JarvisConfig())
            permission_check = lambda: policy.require("microphone.capture")
        permission_check()
        recorder = PvRecorder(device_index=index, frame_length=frame_length)
        recorder.start()
        success = True
        for _ in range(frames_to_read):
            permission_check()
            if len(recorder.read()) != frame_length:
                success = False
                break
    except Exception:
        success = False
    finally:
        if recorder is not None:
            try:
                recorder.stop()
            except Exception:
                success = False
            finally:
                try:
                    recorder.delete()
                except Exception:
                    success = False
    return success
