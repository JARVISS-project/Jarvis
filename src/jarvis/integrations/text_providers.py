"""Explicit text-only APIs. No remote tools, retries, redirects or fallback."""
from __future__ import annotations

import json
import time
from threading import Event, Thread

import httpx


MODELS = {
    'openai': ('gpt-4.1-mini-2025-04-14', 'gpt-4.1-mini'),
    'gemini': ('gemini-3.5-flash-lite',),
}
PRICES = {'openai': (0.40, 1.60), 'gemini': (0.30, 2.50)}
SOURCES = {
    'openai': 'https://developers.openai.com/api/docs/models/gpt-4.1-mini',
    'gemini': 'https://ai.google.dev/gemini-api/docs/pricing',
}
PRICE_DATE = '2026-10-08'

FINISH_REASONS = frozenset({
    'FINISH_REASON_UNSPECIFIED', 'STOP', 'MAX_TOKENS', 'SAFETY', 'RECITATION',
    'LANGUAGE', 'OTHER', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII',
    'MALFORMED_FUNCTION_CALL', 'IMAGE_SAFETY', 'IMAGE_PROHIBITED_CONTENT',
    'IMAGE_RECITATION', 'IMAGE_OTHER', 'NO_IMAGE', 'UNEXPECTED_TOOL_CALL',
    'TOO_MANY_TOOL_CALLS',
})
RPC_CODES = frozenset({
    'INVALID_ARGUMENT', 'FAILED_PRECONDITION', 'OUT_OF_RANGE', 'UNAUTHENTICATED',
    'PERMISSION_DENIED', 'NOT_FOUND', 'RESOURCE_EXHAUSTED', 'INTERNAL',
    'UNAVAILABLE', 'DEADLINE_EXCEEDED', 'UNKNOWN', 'CANCELLED',
})
REJECTION_MESSAGES = {
    'invalid_response': 'Resposta inválida ou não textual.',
    'incomplete': 'Resposta parcial; término incompleto.',
    'safety': 'Resposta bloqueada por segurança.',
    'thought': 'Resposta com raciocínio explícito rejeitada.',
    'remote_tool': 'Resposta com chamada remota de Tool rejeitada.',
    'empty': 'Resposta textual vazia.',
    'http': 'API indisponível.',
    'credential': 'Credencial rejeitada; revise a chave API.',
    'permission': 'Acesso/modelo não autorizado pelo provedor.',
    'credits': 'Créditos/pagamento exigidos pelo provedor; não autoriza cobrança.',
    'quota': 'Cota do provedor atingida; período e créditos não determinados.',
    'retry_hint': 'Limite com espera indicada pelo provedor; cota/créditos não determinados.',
    'limit_unknown': 'Limite do provedor atingido; cota/créditos ou limite temporário não determinados.',
}


class ProviderResponseError(RuntimeError):
    """Local diagnostic containing only fixed identifiers and an HTTP integer."""

    def __init__(self, reason: str, *, http_status: int = 200,
                 finish_reason: object = None, code: object = None):
        self.reason = reason if reason in REJECTION_MESSAGES else 'invalid_response'
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.finish_reason = (finish_reason if isinstance(finish_reason, str)
                              and finish_reason in FINISH_REASONS else None)
        self.code = code if isinstance(code, str) and code in RPC_CODES else None
        super().__init__(self.safe_message())

    def safe_message(self) -> str:
        # Revalidate at the UI/Tool boundary, rather than trusting exception text.
        reason = self.reason if self.reason in REJECTION_MESSAGES else 'invalid_response'
        status = self.http_status if type(self.http_status) is int and 100 <= self.http_status <= 599 else 'unknown'
        finish = self.finish_reason if isinstance(self.finish_reason, str) and self.finish_reason in FINISH_REASONS else 'unknown'
        code = self.code if isinstance(self.code, str) and self.code in RPC_CODES else 'unknown'
        return (f'{REJECTION_MESSAGES[reason]} HTTP={status}; finishReason={finish}; '
                f'code={code}; reason={reason}. Sem reenvio; nenhum retry automático.')


def http_error(provider: str, status: int, value: object) -> ProviderResponseError:
    """Never use provider messages, descriptions, links, IDs or dimensions."""
    reason = {401: 'credential', 403: 'permission', 429: 'limit_unknown'}.get(status, 'http')
    code = None
    if provider == 'gemini':
        if status == 402:
            reason = 'credits'
        error = value.get('error') if isinstance(value, dict) else None
        if isinstance(error, dict):
            code = error.get('status')
            details = error.get('details', [])
            types = {d.get('@type') for d in details if isinstance(d, dict)
                     and isinstance(d.get('@type'), str)} if isinstance(details, list) else set()
            if status == 429 and code == 'RESOURCE_EXHAUSTED':
                if 'type.googleapis.com/google.rpc.QuotaFailure' in types:
                    reason = 'quota'
                elif 'type.googleapis.com/google.rpc.RetryInfo' in types:
                    reason = 'retry_hint'
    return ProviderResponseError(reason, http_status=status, code=code)


def gemini_text(value: dict) -> str:
    """Single-turn text only; thought signatures are ignored, never returned."""
    finish = None
    try:
        feedback = value.get('promptFeedback', {})
        if feedback.get('blockReason') not in (None, 'BLOCK_REASON_UNSPECIFIED'):
            raise ProviderResponseError('safety')
        candidates = value['candidates']
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ValueError
        candidate = candidates[0]
        finish = candidate.get('finishReason')
        if finish != 'STOP':
            reason = ('safety' if finish in {'SAFETY', 'RECITATION', 'BLOCKLIST',
                      'PROHIBITED_CONTENT', 'SPII', 'IMAGE_SAFETY'} else 'incomplete')
            raise ProviderResponseError(reason, finish_reason=finish)
        if any(r.get('blocked') for r in candidate.get('safetyRatings', [])):
            raise ProviderResponseError('safety', finish_reason=finish)
        content = candidate['content']
        if content.get('role', 'model') != 'model':
            raise ValueError
        parts = content['parts']
        if not isinstance(parts, list):
            raise ValueError
        texts = []
        for part in parts:
            if not isinstance(part, dict):
                raise ValueError
            if set(part) & {'functionCall', 'functionResponse', 'executableCode',
                            'codeExecutionResult', 'toolCall', 'toolResponse'}:
                raise ProviderResponseError('remote_tool', finish_reason=finish)
            if (set(part) - {'text', 'thought', 'thoughtSignature'}
                    or not isinstance(part.get('text'), str)
                    or ('thought' in part and type(part['thought']) is not bool)
                    or ('thoughtSignature' in part and not isinstance(part['thoughtSignature'], str))):
                raise ValueError
            if part.get('thought'):
                raise ProviderResponseError('thought', finish_reason=finish)
            texts.append(part['text'])
        text = '\n'.join(texts)
        if not text.strip():
            raise ProviderResponseError('empty', finish_reason=finish)
        if len(text.encode()) > 131072:
            raise ValueError
        return text
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProviderResponseError('invalid_response', finish_reason=finish) from None


def request_spec(provider: str, model: str, payload: str, output: int, key: str):
    if model not in MODELS.get(provider, ()):
        raise ValueError('Modelo não suportado; escolha em Contas e conexões.')
    if provider == 'openai':
        return ('https://api.openai.com/v1/responses', {'Authorization': 'Bearer ' + key},
                {'model': model, 'input': payload, 'max_output_tokens': output,
                 'store': False, 'tools': [], 'tool_choice': 'none', 'truncation': 'disabled'})
    return (f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
            {'x-goog-api-key': key},
            {'contents': [{'role': 'user', 'parts': [{'text': payload}]}],
             'generationConfig': {'maxOutputTokens': output, 'candidateCount': 1}})


def parse_response(provider: str, value: dict) -> str:
    if provider == 'gemini':
        return gemini_text(value)
    try:
        if provider == 'openai':
            if value['status'] != 'completed':
                raise ValueError
            items = value['output']
            if not items or any(i.get('type') != 'message' or i.get('role') != 'assistant' for i in items):
                raise ValueError
            parts = [p for i in items for p in i['content']]
            if any(p.get('type') != 'output_text' for p in parts):
                raise ValueError
            text = '\n'.join(p['text'] for p in parts)
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 131072:
            raise ValueError
        return text
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('Resposta parcial, bloqueada ou inválida; envio não será repetido.') from None


def response_receipt(provider: str, value: dict) -> dict:
    """Only allowlisted model IDs and bounded counters; never retain raw JSON."""
    model = value.get('model' if provider == 'openai' else 'modelVersion')
    usage = value.get('usage' if provider == 'openai' else 'usageMetadata')
    fields = (('input_tokens', 'output_tokens', 'total_tokens') if provider == 'openai'
              else ('promptTokenCount', 'candidatesTokenCount', 'totalTokenCount', 'thoughtsTokenCount'))
    counters = {name: usage[name] for name in fields
                if isinstance(usage, dict) and type(usage.get(name)) is int
                and 0 <= usage[name] <= 2147483647}
    return {'model': model if model in MODELS[provider] else None, 'usage': counters}


class TextProviderAdapter:
    """One HTTP exchange with bounded body, total timeout and cancellation.

    Cancellation releases the caller; an already received request may still be
    billed. The daemon transport closes itself at its bounded network timeout.
    """
    def __init__(self, transport=None):
        self.transport = transport
        self.last_receipt = None

    def generate(self, provider, model, payload, output, timeout, key, cancelled: Event) -> str:
        self.last_receipt = None
        endpoint, headers, body = request_spec(provider, model, payload, output, key)
        completed, result = Event(), []

        def send():
            try:
                with httpx.Client(transport=self.transport, timeout=timeout,
                                  follow_redirects=False, trust_env=False) as client:
                    with client.stream('POST', endpoint, headers=headers, json=body) as response:
                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            if cancelled.is_set():
                                raise RuntimeError('Resposta cancelada ou excede o limite local.')
                            if len(raw) + len(chunk) > 262144:
                                if response.status_code != 200:
                                    raise http_error(provider, response.status_code, None)
                                raise ProviderResponseError('invalid_response')
                            raw.extend(chunk)
                        try:
                            value = json.loads(raw)
                        except (ValueError, UnicodeError):
                            if response.status_code != 200:
                                raise http_error(provider, response.status_code, None) from None
                            raise ProviderResponseError('invalid_response') from None
                        if response.status_code != 200:
                            raise http_error(provider, response.status_code, value)
                        result.append((parse_response(provider, value), response_receipt(provider, value)))
            except httpx.TimeoutException:
                result.append(RuntimeError('Tempo limite; resultado incerto, sem reenvio.'))
            except RuntimeError as error:
                result.append(error)
            except Exception:
                result.append(RuntimeError('Falha de rede/resposta; resultado incerto, sem reenvio.'))
            finally:
                completed.set()

        if cancelled.is_set():
            raise RuntimeError('Cancelado antes do envio.')
        Thread(target=send, name='jarvis-text-api', daemon=True).start()
        deadline = time.monotonic() + timeout
        while not completed.wait(0.05):
            if cancelled.is_set():
                raise RuntimeError('Cancelado; envio recebido pode ser cobrado. Sem estorno confirmado.')
            if time.monotonic() >= deadline:
                cancelled.set()
                raise RuntimeError('Tempo limite; resultado incerto, sem reenvio.')
        if cancelled.is_set():
            raise RuntimeError('Cancelado; envio recebido pode ser cobrado.')
        if isinstance(result[0], Exception):
            raise result[0]
        text, receipt = result[0]
        # Assign on the caller only: a cancelled, late response cannot overwrite it.
        self.last_receipt = receipt
        return text
