"""Human-only, instance-bound cooperative shutdown; never an LLM Tool."""
from threading import RLock
import time
from uuid import uuid4


class RuntimeControl:
    def __init__(self, request_stop, *, clock=time.monotonic):
        self.request_stop = request_stop
        self.clock = clock
        self._pending = {}
        self._requested = False
        self._closed = False
        self._lock = RLock()

    def prepare(self, actor):
        with self._lock:
            if actor is None or self._closed or self._requested:
                raise PermissionError('Encerramento indisponível nesta sessão.')
            now = self.clock()
            self._pending = {k: v for k, v in self._pending.items()
                             if v[0] is not actor and v[1] > now}
            if len(self._pending) >= 16:
                raise PermissionError('Muitas confirmações pendentes.')
            token = uuid4().hex
            self._pending[token] = (actor, now + 60)
            return {'token': token, 'preview':
                    'Encerrar esta instância do JARVIS? A conversa e a captura de voz '
                    'serão encerradas após o trabalho em andamento terminar. '
                    'Operações já realizadas não serão desfeitas.'}

    def finish(self, token, actor, *, cancel=False):
        with self._lock:
            if not isinstance(token, str):
                raise ValueError('Confirmação inválida.')
            pending = self._pending.get(token)
            if (self._closed or self._requested or pending is None or actor is None
                    or pending[0] is not actor or pending[1] <= self.clock()):
                raise PermissionError('Confirmação expirada ou inválida.')
            del self._pending[token]
            if cancel:
                return {'cancelled': True}
            # Consume authority before the callback; never retry an uncertain request.
            self._requested = True
            self._pending.clear()
            self.request_stop()
            return {'requested': True}

    def disconnect(self, actor):
        with self._lock:
            self._pending = {k: v for k, v in self._pending.items() if v[0] is not actor}

    def close(self):
        with self._lock:
            self._closed = True
            self._pending.clear()
