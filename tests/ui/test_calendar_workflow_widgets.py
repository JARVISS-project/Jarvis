"""Offscreen workflow dashboard/settings; synthetic accounts and effects."""
from PySide6.QtWidgets import QPushButton
from threading import Event

from tests.application.test_calendar_workflow import system
from tests.ui.test_administrative_widgets_23bh import close_system, settle


def own_session(window, controller):
    owner = object()
    window.administrative_client.send = lambda payload: controller.handle(
        {**payload, 'authorization': controller.authorization}, window.administrative_client.received.emit, owner=owner)
    return owner


def test_home_proposal_edit_approve_once_and_history(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, _ = values
    own_session(window, controller)
    dialog = None
    try:
        window.nav_buttons['Início'].click()
        inbox = window.workflow_inbox
        settle(app, lambda: not inbox.busy)
        inbox.scan_button.click()
        settle(app, lambda: not inbox.busy)
        assert len(inbox.data['rows']) == 1 and not workflow.adapter.calls
        review = next(button for button in inbox.card_buttons if button.text() == 'Revisar mensagem')
        review.click()
        dialog = inbox.review_dialog
        page = dialog.page
        settle(app, lambda: not page.busy)
        assert 'João' in page.destination.text() and 'WhatsApp' in page.destination.text()
        page.body.setPlainText('Olá, João! Podemos confirmar nosso encontro?')
        page.approve_button.click()
        settle(app, lambda: page.finished and not page.busy and not inbox.busy)
        assert len(workflow.adapter.calls) == 1
        assert workflow.adapter.calls[0][0]['body'] == 'Olá, João! Podemos confirmar nosso encontro?'
        assert inbox.data['rows'][0]['state'] == 'handed_off'
        page.approve_button.click()
        assert len(workflow.adapter.calls) == 1
        window.nav_buttons['Automações'].click()
        settings = window.workflow_page
        settle(app, lambda: not settings.busy)
        assert settings.history.rowCount() == 1
    finally:
        if dialog:
            dialog.reject()
        close_system(h, controller, window)


def test_settings_monitoring_pause_and_rule_explicit_consent(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, _ = values
    own_session(window, controller)
    try:
        window.nav_buttons['Automações'].click()
        page = window.workflow_page
        settle(app, lambda: not page.busy)
        assert not page.enabled.isChecked()
        page.enabled.setChecked(True)
        page.save_button.click()
        settle(app, lambda: not page.busy)
        assert workflow.data['settings']['enabled']
        page.rule_contact.setCurrentIndex(1)
        page.rule_enabled.setChecked(True)
        page.rule_save.click()
        settle(app, lambda: not page.busy)
        assert workflow.data['rules'] == []
        page.rule_accept.setChecked(True)
        page.rule_save.click()
        settle(app, lambda: not page.busy)
        assert len(workflow.data['rules']) == 1 and page.rules.count() == 1
        assert not workflow.adapter.calls
        window.nav_buttons['Início'].click()
        inbox = window.workflow_inbox
        settle(app, lambda: not inbox.busy)
        inbox.pause_button.click()
        settle(app, lambda: not inbox.busy)
        assert not workflow.data['settings']['enabled']
        window.apply_text_event({'type': 'text', 'status': 'disconnected'})
        assert not inbox.timer.isActive() and not inbox.data
    finally:
        close_system(h, controller, window)


def test_review_close_cancels_without_opening_any_app(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, _ = values
    own_session(window, controller)
    try:
        settle(app, lambda: not window.workflow_inbox.busy)
        workflow.scan()
        inbox = window.workflow_inbox
        inbox.refresh()
        settle(app, lambda: not inbox.busy)
        inbox.review(inbox.data['rows'][0])
        page = inbox.review_dialog.page
        settle(app, lambda: not page.busy)
        assert workflow.tickets
        inbox.review_dialog.reject()
        settle(app, lambda: not workflow.tickets)
        assert not workflow.adapter.calls
    finally:
        close_system(h, controller, window)


def test_close_during_fresh_preview_cancels_before_approval(tmp_path):
    values, workflow = system(tmp_path)
    app, h, service, controller, window, _ = values
    own_session(window, controller)
    entered, release = Event(), Event()
    try:
        inbox = window.workflow_inbox
        settle(app, lambda: not inbox.busy)
        workflow.scan()
        inbox.refresh()
        settle(app, lambda: not inbox.busy)
        inbox.review(inbox.data['rows'][0])
        page = inbox.review_dialog.page
        settle(app, lambda: not page.busy)
        original = workflow._fresh
        def slow(row):
            entered.set()
            assert release.wait(3)
            return original(row)
        workflow._fresh = slow
        page.approve_button.click()
        assert entered.wait(2)
        inbox.review_dialog.reject()
        release.set()
        settle(app, lambda: not page.busy and not workflow.tickets)
        assert not workflow.adapter.calls and page.closed
    finally:
        release.set()
        close_system(h, controller, window)
