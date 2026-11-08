"""Validated STT profiles for the local voice runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SttProfile:
    name: str
    model: str
    device: str
    compute_type: str
    allow_device_fallback: bool
    minimum_free_vram_mib: int


_PROFILES = {
    "medium": SttProfile("medium", "medium", "auto", "auto", True, 0),
    "large-v3": SttProfile(
        "large-v3", "large-v3", "cuda", "float16", False, 5_781,
    ),
}


def parse_stt_profile(value: str) -> SttProfile:
    normalized = value.strip().casefold()
    try:
        return _PROFILES[normalized]
    except KeyError as error:
        expected = ", ".join(sorted(_PROFILES))
        raise ValueError(
            f"Perfil STT inválido: {value!r}. Esperado: {expected}."
        ) from error
