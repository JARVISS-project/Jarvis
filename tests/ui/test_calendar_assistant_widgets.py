"""Offscreen interaction, real admin worker, no messages or clipboard writes."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox

from tests.application.test_calendar_assistant import setup_context
from tests.ui.test_administrative_widgets_23bh import close_system, settle
from tests.ui.test_calendar_table import open_page


def test_selected_event_suggestions_draft_whatsapp_and_email_review(tmp_path):
    values, args = setup_context(tmp_path)
    app, h, service, controller, window, boundary = values
    service.calendar_view.local_adviser = lambda context: {'suggestion_ids': ['confirm'], 'preparation_ids': ['questions']}
    dialog = None
    try:
        page = open_page(app, window)
        page.refresh_button.click()
        settle(app, lambda: not page.busy)
        page.search.setText('produto')
        page.table.setCurrentCell(0, 2)
        assert page.suggestions_button.isEnabled()
        page.suggestions_button.click()
        dialog = page.assistant_window
        assistant = dialog.page
        settle(app, lambda: not assistant.busy)
        assert assistant.data['selected'].startswith('local:')
        assert 'WhatsApp preferido' in assistant.contact_hint.text()
        assert assistant.draft_button.isEnabled() and not assistant.copy_button.isEnabled()
        assistant.ai_button.click()
        settle(app, lambda: not assistant.busy)
        assert assistant.suggestions.count() == 1 and 'IA local' in assistant.status.text()
        assistant.draft_button.click()
        settle(app, lambda: not assistant.busy)
        assert assistant.draft_data['channel'] == 'whatsapp'
        assert '+5511999999999' in assistant.draft_label.text()
        assert not assistant.body.isReadOnly() and assistant.copy_button.isEnabled()
        assert assistant.draft_label.textFormat() == Qt.TextFormat.PlainText
        assistant.body.setPlainText('Mensagem revisada por mim.')
        assistant.channel.setCurrentIndex(assistant.channel.findData('email'))
        assert assistant.draft_data is None and not assistant.copy_button.isEnabled()
        assistant.draft_button.click()
        settle(app, lambda: not assistant.busy)
        assert assistant.draft_data['channel'] == 'email' and not assistant.subject.isHidden()
        assert assistant.draft_data['recipient'] == 'joao@example.invalid'
        window.apply_text_event({'type': 'text', 'status': 'disconnected'})
        assert not dialog.isVisible() and not page.rows
        assert h.memory.get_history() == []
    finally:
        if dialog:
            dialog.reject()
        close_system(h, controller, window)


def test_contact_widgets_edit_and_delete_cancel_or_accept(tmp_path):
    values, _args = setup_context(tmp_path)
    app, h, service, controller, window, boundary = values
    dialog = None
    try:
        window.calendar_page.contacts_button.click()
        dialog = window.calendar_page.contacts_window
        page = dialog.page
        settle(app, lambda: not page.busy)
        assert page.rows.count() == 1
        page.rows.setCurrentRow(0)
        page.name.setText('João atualizado')
        page.save_button.click()
        settle(app, lambda: not page.busy)
        assert service.contacts.inventory()['rows'][0]['name'] == 'João atualizado'
        page.rows.setCurrentRow(0)
        page.delete_button.click()
        page.confirmation.reject()
        assert service.contacts.inventory()['rows']
        page.delete_button.click()
        page.confirmation.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Yes).click()
        settle(app, lambda: not page.busy)
        assert not service.contacts.inventory()['rows']
        page.name.setText('Outro contato')
        page.phone.setText('11999999999')
        page.save_button.click()
        assert 'código do país' in page.status.text() and not page.busy
        assert not service.contacts.inventory()['rows']
    finally:
        if dialog:
            dialog.reject()
        close_system(h, controller, window)
