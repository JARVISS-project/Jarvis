from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApplicationInstance:
    application_id: str
    process_id: int
    window_handle: int
    window_class: str

    def identity(self) -> str:
        return (
            f"{self.application_id}|{self.process_id}|"
            f"{self.window_handle}|{self.window_class}"
        )


@dataclass(frozen=True, slots=True)
class ApplicationState:
    application_id: str
    instances: tuple[ApplicationInstance, ...]

    @property
    def is_open(self) -> bool:
        return bool(self.instances)


@dataclass(frozen=True, slots=True)
class PreparedApplicationAction:
    application_id: str
    action: str
    instance: ApplicationInstance | None = None
    catalog_prepared: object | None = None
