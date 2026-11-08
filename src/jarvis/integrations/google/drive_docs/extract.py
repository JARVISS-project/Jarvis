"""Extract bounded plain text, never interpret document instructions."""
from __future__ import annotations

from jarvis.integrations.google.errors import GoogleResponseError


def extract_document(document: dict) -> tuple[str, bool, bool]:
    parts: list[str] = []
    size = nodes = 0
    limited = unsupported = False

    def append(text: str) -> None:
        nonlocal size, limited
        clean = ''.join(c for c in text if c in '\n\t' or ord(c) >= 32)
        remaining = max(0, 40_000 - size)
        parts.append(clean[:remaining])
        size += min(len(clean), remaining)
        limited |= len(clean) > remaining

    def content(elements: list, depth: int = 0) -> None:
        nonlocal nodes, limited, unsupported
        if depth > 20:
            raise GoogleResponseError('Estrutura profunda demais.')
        if not isinstance(elements, list):
            raise GoogleResponseError('Estrutura inválida.')
        for element in elements:
            nodes += 1
            if nodes > 10_000:
                limited = True
                return
            if not isinstance(element, dict):
                raise GoogleResponseError('Elemento inválido.')
            paragraph = element.get('paragraph')
            if isinstance(paragraph, dict):
                if paragraph.get('bullet'):
                    append('- ')
                for run in paragraph.get('elements', []):
                    value = run.get('textRun', {}).get('content')
                    if isinstance(value, str):
                        append(value)
                    else:
                        unsupported = True
            elif isinstance(element.get('table'), dict):
                for row in element['table'].get('tableRows', []):
                    for cell in row.get('tableCells', []):
                        content(cell.get('content', []), depth + 1)
                        append('\t')
                    append('\n')
            elif isinstance(element.get('tableOfContents'), dict):
                content(element['tableOfContents'].get('content', []), depth + 1)
            elif 'sectionBreak' not in element:
                unsupported = True

    def tabs(items: list, depth: int = 0) -> None:
        if depth > 16 or not isinstance(items, list) or len(items) > 64:
            raise GoogleResponseError('Abas inválidas ou acima do limite.')
        for item in items:
            if not isinstance(item, dict):
                raise GoogleResponseError('Aba inválida.')
            body = item.get('documentTab', {}).get('body', {})
            content(body.get('content', []))
            tabs(item.get('childTabs', []), depth + 1)

    if document.get('tabs'):
        tabs(document['tabs'])
    else:
        content(document.get('body', {}).get('content', []))
    # Headers, footnotes, images, equations, comments and suggestions are not rendered.
    unsupported |= any(k in document for k in ('headers', 'footers', 'footnotes', 'inlineObjects'))
    return ''.join(parts), limited, unsupported
