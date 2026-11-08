from __future__ import annotations

from dataclasses import replace

import pytest

from jarvis.config import JarvisConfig
from jarvis.local_control.models import ApplicationInstance, ApplicationState
from jarvis.local_control.service import LocalControlService
from jarvis.tools.local_control import (
    LocalApplicationCloseTool,
    LocalApplicationOpenTool,
    LocalVolumeTool,
)


class FakeBackend:
    def __init__(self) -> None:
        self.available = {"spotify", "obsidian", "default_browser"}
        self.states = {name: ApplicationState(name, ()) for name in self.available}
        self.calls: list[tuple] = []
        self.media = True
        self.volume = True
        self.level = 50
        self.muted = False
        self.close_succeeds = True

    def available_applications(self): return frozenset(self.available)
    def inspect_application(self, app): return self.states[app]
    def open_application(self, app):
        self.calls.append(("open", app))
        self.states[app] = ApplicationState(app, (ApplicationInstance(app, 10, 20, "Window"),))
    def focus_application(self, app, handle): self.calls.append(("focus", app, handle))
    def request_close(self, app, handle):
        self.calls.append(("close", app, handle))
        if self.close_succeeds: self.states[app] = ApplicationState(app, ())
    def media_available(self): return self.media
    def control_media(self, action): self.calls.append(("media", action))
    def volume_available(self): return self.volume
    def get_volume(self): return self.level, self.muted
    def set_volume(self, percent): self.calls.append(("volume", percent)); self.level = percent
    def set_muted(self, muted): self.calls.append(("mute", muted)); self.muted = muted


def service(backend=None, **kwargs): return LocalControlService(backend or FakeBackend(), sleep=lambda _: None, **kwargs)


@pytest.mark.parametrize("invalid", ["cmd", "powershell", "terminal", "spotify.exe", "C:\\x.exe", "https://x"])
def test_application_allowlist_rejects_authority_bearing_values(invalid):
    backend = FakeBackend(); control = service(backend)
    with pytest.raises(PermissionError): control.prepare_application(invalid, "open")
    assert backend.calls == []


def test_open_is_idempotent_and_uses_only_stable_id():
    backend = FakeBackend(); control = service(backend)
    prepared = control.prepare_application("spotify", "open")
    assert control.open_prepared(prepared) == "spotify aberto e verificado."
    assert control.open_prepared(prepared) == "spotify já está aberto."
    assert backend.calls == [("open", "spotify")]


def test_multiple_instances_and_identity_race_block_close():
    backend = FakeBackend()
    one = ApplicationInstance("spotify", 10, 20, "Window")
    backend.states["spotify"] = ApplicationState("spotify", (one, replace(one, process_id=11)))
    with pytest.raises(RuntimeError, match="múltiplas"):
        service(backend).prepare_application("spotify", "close")
    backend.states["spotify"] = ApplicationState("spotify", (one,))
    control = service(backend)
    prepared = control.prepare_application("spotify", "close")
    backend.states["spotify"] = ApplicationState("spotify", (replace(one, process_id=99),))
    with pytest.raises(PermissionError, match="identidade"):
        control.close_prepared(prepared)
    assert backend.calls == []


def test_unresponsive_close_times_out_without_force_kill():
    now = [0.0]
    backend = FakeBackend(); backend.close_succeeds = False
    backend.states["spotify"] = ApplicationState("spotify", (ApplicationInstance("spotify", 10, 20, "Window"),))
    control = LocalControlService(backend, verification_timeout=.1,
        clock=lambda: now[0], sleep=lambda _: now.__setitem__(0, now[0] + .1))
    prepared = control.prepare_application("spotify", "close")
    with pytest.raises(RuntimeError, match="não comprovado"):
        control.close_prepared(prepared)
    assert backend.calls == [("close", "spotify", 20)]


def test_volume_clamps_steps_and_rejects_out_of_range():
    backend = FakeBackend(); backend.level = 98; control = service(backend, volume_step=5)
    assert "100" in control.change_volume("increase")
    backend.level = 2
    assert "0" in control.change_volume("decrease")
    for invalid in (-1, 101, 50.5, True):
        with pytest.raises(ValueError): control.set_volume(invalid)
    assert backend.calls == [("volume", 100), ("volume", 0)]


def test_volume_change_requires_readback_verification():
    backend = FakeBackend()
    backend.set_volume = lambda percent: backend.calls.append(("volume", percent))
    with pytest.raises(RuntimeError, match="não verificada"):
        service(backend).set_volume(40)
    assert backend.level == 50


def test_real_tools_refuse_unavailable_volume():
    backend = FakeBackend(); backend.volume = False
    control = service(backend)
    with pytest.raises(RuntimeError): LocalVolumeTool(control).preflight(action="get")


def test_close_tool_requires_authorized_path():
    backend = FakeBackend(); backend.states["spotify"] = ApplicationState(
        "spotify", (ApplicationInstance("spotify", 10, 20, "Window"),))
    tool = LocalApplicationCloseTool(service(backend))
    with pytest.raises(RuntimeError, match="autorização"):
        tool.execute(application="spotify")
    preparation = tool.prepare(application="spotify")
    with pytest.raises(PermissionError): tool.bind_authorized(preparation, None)


def test_tool_schemas_reject_free_arguments_by_construction():
    tool = LocalApplicationOpenTool(service())
    assert tool.parameters["additionalProperties"] is False
    assert set(tool.parameters["properties"]["application"]["enum"]) == {"spotify", "obsidian", "default_browser"}


def test_feature_flag_defaults_off_and_round_trips(tmp_path):
    settings = tmp_path / "settings.json"
    config = JarvisConfig(settings_path=settings)
    assert config.local_control_enabled is False
    config.save_local_control_enabled(True)
    assert JarvisConfig(settings_path=settings).local_control_enabled is True
    JarvisConfig(settings_path=settings).save_local_control_enabled(False)
    assert JarvisConfig(settings_path=settings).local_control_enabled is False


def test_gsmtc_flag_is_separate_defaults_off_and_round_trips(tmp_path):
    settings = tmp_path / "settings.json"
    config = JarvisConfig(settings_path=settings)
    assert config.gsmtc_enabled is False
    config.save_gsmtc_enabled(True)
    reloaded = JarvisConfig(settings_path=settings)
    assert reloaded.gsmtc_enabled is True
    assert reloaded.local_control_enabled is False
    reloaded.save_gsmtc_enabled(False)
    assert JarvisConfig(settings_path=settings).gsmtc_enabled is False
