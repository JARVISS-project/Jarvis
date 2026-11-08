"""Per-harness selection bound to interaction, local grant and explicit allowlists."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from threading import RLock
import time
from types import MappingProxyType

from jarvis.integrations.google.drive_docs.extract import extract_document
from jarvis.integrations.google.errors import GoogleResponseError

DOC = 'application/vnd.google-apps.document'
FOLDER = 'application/vnd.google-apps.folder'
FIELDS = 'id,name,mimeType,parents,trashed'


def safe_name(value: str) -> str:
    return ' '.join(''.join(c for c in value if ord(c) >= 32).split())[:120]


@dataclass(frozen=True)
class Candidate:
    file_id: str
    name: str


class DriveDocsService:
    def __init__(self, client, files: dict[str, str], folders: dict[str, str],
                 expected_grant: str, clock: Callable[[], float] = time.monotonic,
                 policy_current: Callable[[], bool] | None = None) -> None:
        self.client = client
        self.files = MappingProxyType(dict(files))
        self.folders = MappingProxyType(dict(folders))
        self.expected_grant = expected_grant
        self.clock = clock
        self.policy_current = policy_current
        self.lock = RLock()
        self.clear()

    def clear(self) -> None:
        with self.lock:
            self.interaction = None
            self.candidates: tuple[Candidate, ...] = ()
            self.selected: str | None = None
            self._selected_binding: tuple[str, str, str] | None = None
            self.expires = 0.
            self.offset = 0
            self.revision: str | None = None

    def check_grant(self) -> None:
        if self.policy_current is not None and not self.policy_current():
            self.clear()
            raise PermissionError('Autorizações locais alteradas; reinicie o runtime antes de consultar.')
        if not re.fullmatch(r'[a-f0-9]{64}', self.expected_grant or ''):
            raise PermissionError('Configure o vínculo da conta aprovada.')
        if self.client.grant_binding() != self.expected_grant:
            self.clear()
            raise PermissionError('Conta/grant diferente; selecione novamente após autorização local.')

    def check_selection(self, interaction: str, selected: bool = False) -> None:
        self.check_grant()
        if interaction != self.interaction or self.clock() >= self.expires:
            self.clear()
            raise PermissionError('Seleção expirada ou de outra interação; procure novamente.')
        if selected and (self.selected is None or self.selected not in self.files.values()
                         or self._selected_binding != (interaction, self.expected_grant, self.selected)):
            raise PermissionError('Selecione explicitamente um documento autorizado.')

    def _get(self, service, path, params, deadline):
        self.check_grant()
        if self.clock() >= deadline:
            raise GoogleResponseError('Prazo da operação excedido.')
        result = self.client.get(service, path, params, deadline=deadline)
        self.check_grant()  # local removal while GET is in flight must not release its payload
        if self.clock() >= deadline:
            raise GoogleResponseError('Prazo da operação excedido.')
        return result

    def _metadata(self, file_id, deadline, mime=DOC):
        value = self._get('drive', '/files/' + file_id,
                          {'fields': FIELDS, 'supportsAllDrives': 'true'}, deadline)
        if (value.get('id') != file_id or value.get('mimeType') != mime
                or value.get('trashed') is not False or not isinstance(value.get('name'), str)):
            raise GoogleResponseError('Arquivo indisponível ou tipo não suportado.')
        return value

    def find(self, scope: str, query: str, interaction: str) -> str:
        with self.lock:
            self.check_grant()
            self.clear()
            deadline = self.clock() + 10.
            candidates = []
            if scope == 'autorizados':
                for file_id in dict.fromkeys(self.files.values()):
                    candidates.append(self._metadata(file_id, deadline))
            else:
                folder_id = self.folders[scope]
                self._metadata(folder_id, deadline, FOLDER)
                token = None
                seen = set()
                for _ in range(5):
                    params = {'q': f"'{folder_id}' in parents and trashed = false and mimeType = '{DOC}'",
                              'fields': 'nextPageToken,incompleteSearch,files(' + FIELDS + ')',
                              'pageSize': 20, 'spaces': 'drive', 'corpora': 'user',
                              'includeItemsFromAllDrives': 'true', 'supportsAllDrives': 'true'}
                    if token:
                        params['pageToken'] = token
                    page = self._get('drive', '/files', params, deadline)
                    if page.get('incompleteSearch') or not isinstance(page.get('files'), list):
                        raise GoogleResponseError('Busca incompleta ou inválida.')
                    for item in page['files']:
                        if not isinstance(item, dict):
                            raise GoogleResponseError('Metadados inválidos.')
                        if (item.get('id') in self.files.values() and item.get('mimeType') == DOC
                                and item.get('trashed') is False and folder_id in item.get('parents', [])
                                and isinstance(item.get('name'), str)):
                            candidates.append(item)
                    token = page.get('nextPageToken')
                    if token is None:
                        break
                    if not isinstance(token, str) or not token or token in seen:
                        raise GoogleResponseError('Paginação inválida.')
                    seen.add(token)
                else:
                    raise GoogleResponseError('Limite de cinco páginas; consulta não concluída.')
            unique = {item['id']: Candidate(item['id'], safe_name(item['name'])) for item in candidates
                      if query.casefold() in item['name'].casefold()}
            self.candidates = tuple(unique.values())[:20]
            self.interaction = interaction
            self.expires = self.clock() + 120.
            if not self.candidates:
                return 'Nenhum documento autorizado encontrado.'
            return ('Candidatos autorizados (selecione pelo número; nenhum escolhido automaticamente):\n'
                    + '\n'.join(f'{i}: {item.name}' for i, item in enumerate(self.candidates, 1)))

    def select(self, interaction: str, index: int) -> str:
        with self.lock:
            self.check_selection(interaction)
            if not 1 <= index <= len(self.candidates):
                raise PermissionError('Candidato inválido.')
            self.selected = self.candidates[index - 1].file_id
            self._selected_binding = (interaction, self.expected_grant, self.selected)
            self.offset = 0
            self.revision = None
            return 'Documento selecionado. Peça leitura ou metadados; seleção válida por até dois minutos.'

    def metadata(self, interaction: str) -> str:
        with self.lock:
            self.check_selection(interaction, True)
            data = self._metadata(self.selected, self.clock() + 10.)
            return safe_name(data['name']) + '\nTipo: Google Docs (autorizado).'

    def read(self, interaction: str, continuation: bool = False) -> str:
        with self.lock:
            self.check_selection(interaction, True)
            deadline = self.clock() + 10.
            self._metadata(self.selected, deadline)  # revalidate membership/type/permission each read
            value = self._get('docs', '/documents/' + self.selected,
                              {'includeTabsContent': 'true'}, deadline)
            if value.get('documentId') != self.selected:
                raise GoogleResponseError('Identidade do documento divergente.')
            revision = value.get('revisionId')
            if continuation and (self.offset == 0 or not revision or revision != self.revision):
                self.clear()
                raise PermissionError('Documento alterado ou continuação inválida; procure novamente.')
            text, limited, unsupported = extract_document(value)
            start = self.offset if continuation else 0
            fragment = text[start:start + 3500]
            self.offset = start + len(fragment)
            self.revision = revision
            suffix = '\nHá continuação: diga continue o documento selecionado.' if self.offset < len(text) else '\nFim do texto extraído.'
            if limited:
                suffix += '\nExtração limitada a 40000 caracteres/10000 elementos.'
            if unsupported:
                suffix += '\nConteúdo não textual e estruturas não suportadas foram omitidos.'
            return (fragment or 'Documento vazio (sem texto extraível).') + suffix
