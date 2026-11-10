"""User-entered local contacts, protected at rest; no address-book discovery."""
from __future__ import annotations

import hashlib
import json
import re
from uuid import uuid4

from jarvis.integrations.google.storage import WindowsDPAPIProtector


def email_address(value: str) -> str:
    value = value.strip().casefold()
    if value and (len(value) > 254 or not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}', value)):
        raise ValueError('E-mail inválido.')
    return value


def whatsapp_number(value: str) -> str:
    value = re.sub(r'[ ()-]', '', value.strip())
    if value and not re.fullmatch(r'\+[1-9][0-9]{7,14}', value):
        raise ValueError('Informe o WhatsApp com código do país, como +55…')
    return value


class ContactService:
    def __init__(self, path, protector=None):
        self.path = path
        self.protector = protector

    def _read(self):
        if not self.path.exists():
            return []
        try:
            raw = self.path.read_bytes()
            if len(raw) > 65536:
                raise ValueError
            protector = self.protector or WindowsDPAPIProtector()
            rows = json.loads(protector.unprotect(raw))
            if not isinstance(rows, list) or len(rows) > 200:
                raise ValueError
            ids, emails = set(), set()
            for row in rows:
                if set(row) != {'id', 'name', 'email', 'whatsapp'} or not re.fullmatch(r'[a-f0-9]{32}', row['id']):
                    raise ValueError
                self._validate(row['name'], row['email'], row['whatsapp'])
                if row['id'] in ids or row['email'] and row['email'] in emails:
                    raise ValueError
                ids.add(row['id'])
                if row['email']:
                    emails.add(row['email'])
            return rows
        except Exception:
            raise PermissionError('Contatos protegidos indisponíveis; nenhum contato será usado.') from None

    @staticmethod
    def _validate(name, email, whatsapp):
        if not all(isinstance(value, str) for value in (name, email, whatsapp)):
            raise ValueError('Contato inválido.')
        if not name.strip() or len(name) > 120 or any(ord(char) < 32 for char in name):
            raise ValueError('Informe um nome de até 120 caracteres.')
        if not email_address(email) and not whatsapp_number(whatsapp):
            raise ValueError('Informe e-mail ou WhatsApp.')

    def inventory(self):
        rows = self._read()
        revision = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        return {'rows': rows, 'revision': revision}

    def save(self, id, name, email, whatsapp, revision):
        current = self.inventory()
        if revision != current['revision']:
            raise PermissionError('Contatos mudaram; recarregue antes de salvar.')
        self._validate(name, email, whatsapp)
        email, whatsapp = email_address(email), whatsapp_number(whatsapp)
        rows = current['rows']
        if id is not None and not any(row['id'] == id for row in rows):
            raise ValueError('Contato indisponível.')
        if any(row['email'] == email and email and row['id'] != id for row in rows):
            raise ValueError('Este e-mail já pertence a um contato cadastrado.')
        if id is None and len(rows) >= 200:
            raise ValueError('Limite de 200 contatos atingido.')
        contact = {'id': id or uuid4().hex, 'name': name.strip(), 'email': email, 'whatsapp': whatsapp}
        rows = [row for row in rows if row['id'] != id] + [contact]
        self._write(rows)
        return self.inventory()

    def delete(self, id, revision):
        current = self.inventory()
        if revision != current['revision'] or not any(row['id'] == id for row in current['rows']):
            raise PermissionError('Contato mudou; recarregue antes de remover.')
        self._write([row for row in current['rows'] if row['id'] != id])
        return self.inventory()

    def _write(self, rows):
        # Write protected bytes atomically; never write plaintext to disk.
        import os
        import tempfile
        protector = self.protector or WindowsDPAPIProtector()
        payload = json.dumps(rows, ensure_ascii=False).encode()
        if len(payload) > 60000:
            raise ValueError('Os contatos excedem o limite de armazenamento protegido.')
        raw = protector.protect(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
