"""Administrative widgets: literal text, typed local services, no providers."""
from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)


class AdministrativeClient(QObject):
    received = Signal(object)

    def __init__(self, send=None, parent=None) -> None:
        super().__init__(parent)
        self.send = send
        self.generation = None
        self._pending = {}
        self.received.connect(self.apply_event)

    def submit(self, method: str, arguments: dict, callback) -> None:
        if not self.generation or not callable(self.send):
            callback({"status": "error", "error": "Runtime desconectado; serviço administrativo indisponível."})
            return
        interaction = uuid4().hex
        self._pending[interaction] = callback
        try:
            self.send({"command": "admin_request", "generation": self.generation,
                       "interaction": interaction, "method": method, "arguments": arguments})
        except Exception:
            self._pending.pop(interaction, None)
            callback({"status": "error", "error": "Pedido administrativo não enviado; nenhuma operação confirmada."})

    def apply_event(self, event: dict) -> None:
        if event.get("status") == "disconnected" or (event.get("status") == "ready" and event.get("generation") != self.generation):
            pending, self._pending = self._pending, {}
            for callback in pending.values():
                callback({"status": "error", "error": "Runtime reiniciado/desconectado; operação não será reenviada."})
            self.generation = event.get("generation") if event.get("status") == "ready" else None
        if event.get("type") == "admin" and event.get("generation") == self.generation:
            callback = self._pending.pop(event.get("interaction"), None)
            if callback:
                callback(event)


def literal_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class AdministrativePage(QWidget):
    def __init__(self, client: AdministrativeClient, parent=None) -> None:
        super().__init__(parent)
        self.client = client
        self.layout = QVBoxLayout(self)
        self.status = literal_label("Pronto para carregar dados locais.")
        self.layout.addWidget(self.status)
        self.busy = False
        self.controls = []

    def request(self, method: str, arguments: dict, success) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.setText("Carregando…")
        for control in self.controls:
            control.setEnabled(False)
        def finished(event):
            self.busy = False
            for control in self.controls:
                control.setEnabled(True)
            if event.get("status") != "done":
                self.status.setText(event.get("error", "Falha no serviço; nenhuma operação confirmada."))
            else:
                success(event["data"])
            self.after_request()
        self.client.submit(method, arguments, finished)

    def after_request(self) -> None:
        pass


class PermissionsPage(AdministrativePage):
    def __init__(self, client, parent=None) -> None:
        super().__init__(client, parent)
        self.layout.addWidget(literal_label("O que o JARVIS pode acessar?\nConfiguração, credencial, autorização operacional e prontidão são diferentes. Bloqueio explícito prevalece. Revogação não desfaz efeitos já realizados."))
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar recurso ou ação")
        self.groups = QComboBox()
        self.groups.addItem("Todos os grupos")
        self.refresh_button = QPushButton("Atualizar")
        self.refresh_button.clicked.connect(self.refresh)
        filters.addWidget(self.search)
        filters.addWidget(self.groups)
        filters.addWidget(self.refresh_button)
        self.layout.addLayout(filters)
        self.rows = QListWidget()
        self.rows.currentItemChanged.connect(self.show_selected)
        self.layout.addWidget(self.rows, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(160)
        self.layout.addWidget(self.details)
        buttons = QHBoxLayout()
        self.mode = QComboBox()
        self.expires = QLineEdit()
        self.expires.setPlaceholderText("Validade ISO com timezone; vazio = até revogar")
        self.apply_button = QPushButton("Aplicar ao recurso selecionado")
        self.apply_button.clicked.connect(self.apply_selected)
        buttons.addWidget(self.mode)
        buttons.addWidget(self.expires)
        buttons.addWidget(self.apply_button)
        self.layout.addLayout(buttons)
        self.descendant = QLineEdit()
        self.descendant.setPlaceholderText("Opcional: caminho absoluto descendente de uma raiz configurada")
        self.layout.addWidget(self.descendant)
        self.search.textChanged.connect(self.filter_rows)
        self.groups.currentTextChanged.connect(self.filter_rows)
        self.controls = [self.search, self.groups, self.refresh_button, self.apply_button, self.mode, self.expires, self.descendant]
        self._data = {"rows": [], "revision": "", "valid": False}

    def refresh(self) -> None:
        self.request("permissions.list", {}, self.loaded)

    def loaded(self, data: dict) -> None:
        self._data = data
        self.groups.blockSignals(True)
        self.groups.clear()
        self.groups.addItems(["Todos os grupos", *sorted({r["group"] for r in data["rows"]})])
        self.groups.blockSignals(False)
        self.filter_rows()
        self.status.setText("Configuração inválida: operações gerenciadas bloqueadas; restaure o arquivo administrativo válido." if not data["valid"] else "Política local carregada. Permitir preserva os limites e confirmações existentes.")

    def filter_rows(self) -> None:
        self.rows.clear()
        query, group = self.search.text().casefold(), self.groups.currentText()
        for row in self._data["rows"]:
            if group not in {"Todos os grupos", row["group"]} or query not in " ".join(str(v) for v in row.values()).casefold():
                continue
            item = QListWidgetItem(f'{row["group"]} · {row["title"]} · {row["operation"]} — {row["effective"]}')
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.rows.addItem(item)
        if self.rows.count():
            self.rows.setCurrentRow(0)
        else:
            self.details.setPlainText("Nenhum recurso configurado corresponde à busca.")
            self.apply_button.setEnabled(False)

    def show_selected(self, item, _previous=None) -> None:
        self.mode.clear()
        if item is None:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        labels = {"block": "Bloquear", "ask": "Perguntar a cada operação", "allow": "Permitir no escopo exibido"}
        for mode in row["modes"]:
            self.mode.addItem(labels[mode], mode)
        self.mode.setCurrentIndex(max(0, self.mode.findData(row["mode"])))
        self.apply_button.setEnabled(bool(row["modes"]) and self._data["valid"] and not self.busy)
        self.descendant.setEnabled(row["resource"].startswith("path:"))
        self.details.setPlainText("\n".join((f'Recurso: {row["title"]}', f'Ações: {row["operation"]}', f'Escopo: {row["scope"]}',
            f'Duração: {row["duration"]}', f'Origem: {row["origin"]}', f'Política efetiva: {row["effective"]}',
            f'Credencial: {row["credential"]}', f'Prontidão: {row["readiness"]}')))

    def apply_selected(self) -> None:
        item = self.rows.currentItem()
        if item is None or self.mode.currentData() is None:
            return
        row = item.data(Qt.ItemDataRole.UserRole)
        resource = "path:" + self.descendant.text().strip() if row["resource"].startswith("path:") and self.descendant.text().strip() else row["resource"]
        self.request("permissions.set", {"resource": resource, "action": row["action"], "mode": self.mode.currentData(),
            "revision": self._data["revision"], "expires": self.expires.text().strip() or None}, self.loaded)


class MemoryPage(AdministrativePage):
    def __init__(self, client, parent=None) -> None:
        super().__init__(client, parent)
        self.layout.addWidget(literal_label("Memória explícita: preferências, fatos e tarefas. Memória não concede permissões. Não guarde credenciais."))
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar nas memórias locais")
        self.search.returnPressed.connect(self.refresh)
        self.refresh_button = QPushButton("Buscar / atualizar")
        self.refresh_button.clicked.connect(self.refresh)
        filters.addWidget(self.search)
        filters.addWidget(self.refresh_button)
        self.layout.addLayout(filters)
        self.temporary = QCheckBox("Sessão temporária: impedir novas gravações até encerrar o runtime")
        self.temporary.clicked.connect(lambda checked: self.request("memory.session", {"temporary": checked}, self.loaded))
        self.layout.addWidget(self.temporary)
        self.rows = QListWidget()
        self.rows.currentItemChanged.connect(self.select_entry)
        self.layout.addWidget(self.rows, 1)
        self.metadata = literal_label("Nenhum registro selecionado.")
        self.layout.addWidget(self.metadata)
        self.content = QPlainTextEdit()
        self.content.setPlaceholderText("Conteúdo literal da memória")
        self.content.setMaximumHeight(120)
        self.layout.addWidget(self.content)
        controls = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItems(["preferência", "fato", "tarefa"])
        self.retention = QSpinBox()
        self.retention.setRange(0, 36500)
        self.retention.setSpecialValueText("Sem prazo")
        self.retention.setSuffix(" dias")
        self.context = QCheckBox("Usar no contexto local")
        self.context.setChecked(True)
        self.external = QCheckBox("Compartilhar externamente (exige permissão separada)")
        for widget in (self.category, self.retention, self.context, self.external):
            controls.addWidget(widget)
        self.layout.addLayout(controls)
        buttons = QHBoxLayout()
        self.new_button = QPushButton("Nova memória")
        self.new_button.clicked.connect(self.new_entry)
        self.save_button = QPushButton("Guardar / salvar edição")
        self.save_button.clicked.connect(self.save)
        self.forget_button = QPushButton("Esquecer…")
        self.forget_button.clicked.connect(self.prepare_forget)
        for widget in (self.new_button, self.save_button, self.forget_button):
            buttons.addWidget(widget)
        self.layout.addLayout(buttons)
        self.controls = [self.search, self.refresh_button, self.temporary, self.rows, self.content, self.category, self.retention,
                         self.context, self.external, self.new_button, self.save_button, self.forget_button]
        self.selected = None
        self.confirmation = None
        self.new_entry()

    def refresh(self) -> None:
        self.request("memory.list", {"query": self.search.text()}, self.loaded)

    def loaded(self, data: dict) -> None:
        self.rows.clear()
        self.temporary.setChecked(data["temporary"])
        for row in data["rows"]:
            item = QListWidgetItem(f'{row["category"]} · {row["content"][:100]}')
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.rows.addItem(item)
        self.new_entry()
        self.save_button.setEnabled(not data["temporary"])
        self.status.setText(f'{data["total"]} memórias encontradas. {data["limit"]}' if data["rows"] else "Nenhuma memória encontrada. Use Nova memória para incluir explicitamente.")

    def select_entry(self, item, _previous=None) -> None:
        if item is None:
            self.selected = None
            return
        self.selected = row = item.data(Qt.ItemDataRole.UserRole)
        self.content.setPlainText(row["content"])
        if self.category.findText(row["category"]) == -1:
            self.category.addItem(row["category"])
        self.category.setCurrentText(row["category"])
        self.retention.setValue(row["retention_days"] or 0)
        self.context.setChecked(row["context"])
        self.external.setChecked(row["external"])
        self.metadata.setText(f'Origem: {row["origin"]} · Data: {row["created"]} · Validade: {row["retention_days"] or "sem prazo"}')
        self.forget_button.setEnabled(not self.busy)

    def new_entry(self) -> None:
        self.rows.clearSelection()
        self.selected = None
        self.content.clear()
        self.category.clear()
        self.category.addItems(["preferência", "fato", "tarefa"])
        self.category.setCurrentIndex(0)
        self.retention.setValue(0)
        self.context.setChecked(True)
        self.external.setChecked(False)
        self.metadata.setText("Inclusão explícita; sem compartilhamento externo por padrão.")
        self.forget_button.setEnabled(False)

    def save(self) -> None:
        self.request("memory.save", {"id": self.selected["id"] if self.selected else None,
            "version": self.selected["version"] if self.selected else None, "content": self.content.toPlainText(),
            "category": self.category.currentText(), "context": self.context.isChecked(), "external": self.external.isChecked(),
            "retention_days": self.retention.value() or None}, self.loaded)

    def prepare_forget(self) -> None:
        if self.selected:
            self.request("memory.prepare_forget", {"id": self.selected["id"]}, self.show_forget)

    def show_forget(self, prepared: dict) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Confirmar esquecimento")
        layout = QVBoxLayout(dialog)
        layout.addWidget(literal_label(prepared["preview"]))
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText(prepared["content"])
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(lambda: (dialog.accept(), self.request("memory.forget", {"token": prepared["token"]}, self.loaded)))
        self.confirmation = dialog
        self.status.setText("Confirmação explícita pendente por até 60 segundos.")
        dialog.open()
