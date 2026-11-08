from __future__ import annotations

import time
from collections.abc import Callable

from jarvis.local_control.backend import LocalControlBackend
from jarvis.local_control.models import (
    ApplicationInstance,
    ApplicationState,
    PreparedApplicationAction,
)


APPLICATION_IDS = frozenset({"spotify", "obsidian", "default_browser"})


class LocalControlService:
    def __init__(
        self,
        backend: LocalControlBackend,
        *,
        volume_step: int = 5,
        verification_timeout: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        application_control=None,
    ) -> None:
        if not 1 <= volume_step <= 25:
            raise ValueError("volume_step deve estar entre 1 e 25")
        self.backend = backend
        self.volume_step = volume_step
        self.verification_timeout = verification_timeout
        self.clock = clock
        self.sleep = sleep
        self.application_control = application_control

    def validate_application_id(self, application_id: str) -> str:
        if application_id not in APPLICATION_IDS:
            raise PermissionError("Aplicativo fora da allowlist.")
        if application_id not in self.backend.available_applications():
            raise RuntimeError("Aplicativo permitido, mas indisponível neste sistema.")
        return application_id

    def state(self, application_id: str) -> ApplicationState:
        if self.application_control is not None:
            from jarvis.local_control.models import ApplicationInstance
            entry = self._catalog_entry(application_id)
            prepared = self.application_control.prepare(entry.id, 'status')
            return ApplicationState(application_id, tuple(ApplicationInstance(application_id, w.pid, w.hwnd, w.class_name)
                for w in self.application_control.backend.windows(entry)))
        return self.backend.inspect_application(
            self.validate_application_id(application_id)
        )

    def prepare_application(
        self, application_id: str, action: str,
    ) -> PreparedApplicationAction:
        if self.application_control is not None:
            entry = self._catalog_entry(application_id)
            return PreparedApplicationAction(application_id, action, catalog_prepared=self.application_control.prepare(entry.id, action))
        application_id = self.validate_application_id(application_id)
        state = self.backend.inspect_application(application_id)
        if action in {"open", "status"}:
            return PreparedApplicationAction(application_id, action)
        if len(state.instances) != 1:
            detail = "não está aberto" if not state.instances else "possui múltiplas instâncias"
            raise RuntimeError(f"{application_id} {detail}; alvo seguro não comprovado.")
        return PreparedApplicationAction(application_id, action, state.instances[0])

    def revalidate(self, prepared: PreparedApplicationAction) -> None:
        if prepared.catalog_prepared is not None:
            self.application_control.revalidate(prepared.catalog_prepared)
            return
        self.validate_application_id(prepared.application_id)
        if prepared.instance is None:
            return
        current = self.backend.inspect_application(prepared.application_id)
        if current.instances != (prepared.instance,):
            raise PermissionError("A identidade do aplicativo mudou após o preparo.")

    def open_prepared(self, prepared: PreparedApplicationAction) -> str:
        if prepared.catalog_prepared is not None:
            return self.application_control.execute(prepared.catalog_prepared)
        self.revalidate(prepared)
        before = self.backend.inspect_application(prepared.application_id)
        if before.is_open:
            return f"{prepared.application_id} já está aberto."
        self.backend.open_application(prepared.application_id)
        self._wait_for(lambda: self.backend.inspect_application(
            prepared.application_id).is_open, "abertura não verificada")
        return f"{prepared.application_id} aberto e verificado."

    def focus_prepared(self, prepared: PreparedApplicationAction) -> str:
        if prepared.catalog_prepared is not None:
            return self.application_control.execute(prepared.catalog_prepared)
        self.revalidate(prepared)
        assert prepared.instance is not None
        self.backend.focus_application(
            prepared.application_id, prepared.instance.window_handle,
        )
        self.revalidate(prepared)
        return f"{prepared.application_id} trazido para frente."

    def close_prepared(self, prepared: PreparedApplicationAction) -> str:
        if prepared.catalog_prepared is not None:
            return self.application_control.execute(prepared.catalog_prepared)
        self.revalidate(prepared)
        assert prepared.instance is not None
        self.backend.request_close(
            prepared.application_id, prepared.instance.window_handle,
        )
        self._wait_for(
            lambda: not self.backend.inspect_application(
                prepared.application_id).instances,
            "fechamento gracioso não comprovado",
        )
        return f"{prepared.application_id} fechado de forma graciosa e verificada."

    def get_volume(self) -> tuple[int, bool]:
        if not self.backend.volume_available():
            raise RuntimeError("Controle de volume indisponível.")
        percent, muted = self.backend.get_volume()
        if not 0 <= percent <= 100:
            raise RuntimeError("Backend retornou volume fora da faixa.")
        return percent, muted

    def _catalog_entry(self, application_id):
        if application_id not in APPLICATION_IDS:
            raise PermissionError('ID legado não permitido.')
        if not any(e.legacy == application_id for e in self.application_control.catalog.entries()):
            self.application_control.initialize_legacy()
        matches = [entry for entry in self.application_control.catalog.entries() if entry.legacy == application_id]
        if len(matches) != 1:
            raise RuntimeError('Aplicativo legado não disponível no catálogo validado.')
        return self.application_control.catalog.get(matches[0].id)

    def change_volume(self, direction: str) -> str:
        if direction not in {"increase", "decrease"}:
            raise ValueError("Direção de volume inválida.")
        current, _ = self.get_volume()
        delta = self.volume_step if direction == "increase" else -self.volume_step
        target = max(0, min(100, current + delta))
        self.backend.set_volume(target)
        self._verify_volume(target)
        return f"Volume definido em {target} por cento."

    def set_volume(self, percent: int) -> str:
        if not isinstance(percent, int) or isinstance(percent, bool):
            raise ValueError("Percentual inteiro obrigatório.")
        if not 0 <= percent <= 100:
            raise ValueError("Volume deve estar entre 0 e 100.")
        if not self.backend.volume_available():
            raise RuntimeError("Controle de volume indisponível.")
        self.backend.set_volume(percent)
        self._verify_volume(percent)
        return f"Volume definido em {percent} por cento."

    def set_muted(self, muted: bool) -> str:
        if not isinstance(muted, bool):
            raise ValueError("Estado de mudo inválido.")
        if not self.backend.volume_available():
            raise RuntimeError("Controle de volume indisponível.")
        self.backend.set_muted(muted)
        _percent, observed = self.get_volume()
        if observed is not muted:
            raise RuntimeError("Alteração de mudo não verificada.")
        return "Mudo ativado." if muted else "Mudo desativado."

    def _verify_volume(self, expected: int) -> None:
        observed, _muted = self.get_volume()
        if abs(observed - expected) > 1:
            raise RuntimeError("Alteração de volume não verificada.")

    def _wait_for(self, predicate: Callable[[], bool], error: str) -> None:
        deadline = self.clock() + self.verification_timeout
        while not predicate():
            if self.clock() >= deadline:
                raise RuntimeError(error)
            self.sleep(min(0.05, self.verification_timeout))
