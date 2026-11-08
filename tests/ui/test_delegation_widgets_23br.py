import json

from jarvis.application.delegation import DelegationService
from tests.integration.test_administration_23bh import change
from tests.integration.test_delegation_23br import Protector
from tests.ui.test_administrative_widgets_23bh import window_system, close_system, settle


def test_accounts_api_widgets_protected_settings_preview_edit_and_safe_text(tmp_path):
    app, h, admin, controller, window = window_system(tmp_path)
    h.delegation = admin.delegation = DelegationService(h, Protector())
    sends = []
    h._execute_tool = lambda *a, **kw: sends.append((a, kw))
    try:
        page = window.accounts_page.delegation
        commands = []
        page.submit_command = commands.append
        page.refresh()
        settle(app, lambda: not page.busy and bool(page.data))
        assert 'Indisponível' in page.availability.text()
        page.key.setText('neutral-key-fixture')
        page.save()
        settle(app, lambda: not page.busy and h.delegation.path.exists())
        assert not page.key.text() and 'neutral-key-fixture' not in json.dumps(page.data)
        for r in ('model.external', 'delegation.execute'):
            change(admin, r, 'allow')
        page.task.setPlainText('Explain gravity simply.')
        page.prepare()
        settle(app, lambda: not page.busy and page.token is not None)
        assert page.execute_button.isEnabled() and 'Explain gravity simply.' in page.preview.text()
        assert 'preços pagos' in page.preview.text()
        assert 'não verifica o plano da conta' in page.preview.text()
        assert 'não comprova gratuidade' in page.preview.text()
        assert not sends and not commands
        page.task.setPlainText('Changed neutral task')
        assert not page.token and not page.execute_button.isEnabled()
        settle(app, lambda: not h.delegation.drafts)
        page.prepare()
        settle(app, lambda: not page.busy and page.token is not None)
        page.execute()
        assert len(commands) == 1 and not sends
        page.provider.setCurrentText('gemini')
        assert not page.token and not page.execute_button.isEnabled()
        assert 'Indisponível' in page.availability.text()
    finally:
        close_system(h, controller, window)
