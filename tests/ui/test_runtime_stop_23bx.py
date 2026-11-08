from uuid import uuid4

import pytest
from PySide6.QtWidgets import QDialogButtonBox

from jarvis.application.runtime_control import RuntimeControl
from tests.application.test_runtime_control_23bx import Owner
from tests.ui.test_administrative_widgets_23bh import window_system, close_system, settle


@pytest.mark.parametrize('action', ['confirm', 'cancel', 'disconnect', 'restart', 'expired'])
def test_shutdown_dialog_binds_to_live_connection_without_automatic_effect(tmp_path, action):
    app, harness, service, controller, window = window_system(tmp_path)
    calls, now = [], [100.0]
    control = RuntimeControl(lambda: calls.append(True), clock=lambda: now[0])
    service.runtime_control = control
    owner = Owner()
    def send(payload):
        controller.handle({**payload, 'authorization': controller.authorization},
                          window.administrative_client.received.emit, owner=owner)
    window.administrative_client.send = send
    try:
        page = window.runtime_stop
        assert page.button.isEnabled()
        page.button.click()
        settle(app, lambda: page.confirmation is not None and not page.busy)
        dialog = page.confirmation
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons.button(QDialogButtonBox.StandardButton.Cancel).isDefault()
        assert not calls and not page.button.isEnabled()
        if action == 'disconnect':
            controller.disconnect(owner)
            window.apply_text_event({'status': 'disconnected'})
            assert page.confirmation is None and not page.button.isEnabled()
        elif action == 'restart':
            window.apply_text_event({'status': 'ready', 'generation': uuid4().hex})
            assert page.confirmation is None
        else:
            if action == 'expired':
                now[0] += 61
            buttons.button(QDialogButtonBox.StandardButton.Cancel if action == 'cancel'
                           else QDialogButtonBox.StandardButton.Ok).click()
            settle(app, lambda: not page.busy)
            assert page.confirmation is None
            assert page.requested is (action == 'confirm')
            assert page.button.isEnabled() is (action != 'confirm')
        assert calls == ([True] if action == 'confirm' else [])
    finally:
        close_system(harness, controller, window)
