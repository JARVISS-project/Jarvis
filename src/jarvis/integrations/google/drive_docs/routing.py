"""Anchored text grammar; candidates are never picked by the LLM."""
import re
from uuid import uuid4


def looks_private_document(text: str) -> bool:
    return bool(re.search(r'\b(?:documentos?|drive|docs)\b', text.casefold()))


def dispatch(harness, message: str) -> str | None:
    tool = harness.tool_registry.get('google_drive_find_files')
    service = tool.service if tool is not None else None
    text = ' '.join(message.casefold().strip().rstrip('.?!').split())
    if not looks_private_document(text):
        if service is not None:
            service.clear()  # unrelated intent ends this selection interaction
        return None
    harness.last_response_private_google = True
    if service is None:
        return 'Google Drive/Docs desligado; não consultei arquivos.'
    if (re.search(r'\b(?:não|nao|nunca|jamais|e|depois|também|tambem)\b', text)
            or re.search(r'https?://|[/\\]|\b(?:abra|crie|apague|edite|envie|execute)\b', text)):
        service.clear()
        return 'Peça uma única operação de leitura, sem negação, URL ou ID.'
    arguments = None
    match = re.fullmatch(r'(?:procure|busque) o documento (.{1,120})', text)
    if match:
        name = match[1]
        if not re.fullmatch(r'[\wÀ-ÿ -]+', name):
            return 'Nome de busca inválido.'
        arguments = {'scope': 'autorizados', 'query': name, 'interaction': uuid4().hex}
    else:
        match = re.fullmatch(r'liste os documentos(?: da pasta ([a-z][a-z0-9_]{0,31})| autorizados)', text)
        if match:
            arguments = {'scope': match[1] or 'autorizados', 'query': '', 'interaction': uuid4().hex}
    if arguments is None:
        match = re.fullmatch(r'selecione (?:o )?documento ([1-9]|1[0-9]|20)', text)
        if match:
            name = 'google_drive_select_file'
            arguments = {'index': int(match[1]), 'interaction': service.interaction or ''}
        elif text in {'leia o documento selecionado', 'continue o documento selecionado',
                      'mostre os metadados do documento selecionado'}:
            name = 'google_drive_metadata' if text.startswith('mostre') else 'google_docs_read'
            arguments = {'interaction': service.interaction or ''}
            if name == 'google_docs_read':
                arguments['continuation'] = text.startswith('continue')
        else:
            service.clear()
            return 'Use procure o documento NOME, liste os documentos autorizados/da pasta ALIAS, selecione documento NÚMERO ou leia o documento selecionado.'
        tool = harness.tool_registry.get(name)
        if tool is None:
            return 'Capacidade de leitura desligada; nenhuma consulta executada.'
    result = harness._execute_tool(tool, arguments)
    if getattr(harness, '_calendar_response_mode', 'text') == 'voice' and result.status == 'success':
        if tool.name == 'google_docs_read':
            return result.content[:450] + ' Trecho de voz limitado; detalhes por texto.'
        return result.content[:600]
    return result.content
