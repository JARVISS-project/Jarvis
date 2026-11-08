"""Adversarial checks for the isolated 23AC policy."""

from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.evaluate_semantics_23ac import preserved
from scripts.semantic_resolver_23ac import Catalog, parse, resolve
from jarvis.config import JarvisConfig


@pytest.fixture
def catalog(tmp_path) -> Catalog:
    return Catalog.from_checkout(JarvisConfig(settings_path=tmp_path / "missing.json"))


def with_desktop_control(catalog: Catalog) -> Catalog:
    tools = dict(catalog.tools)
    for name in ("desktop_open_application", "desktop_close_window"):
        tools[name] = replace(tools[name], enabled=True)
    return Catalog(tools, set(catalog.applications))


@pytest.mark.parametrize("left,right", [
    ("Abra o notepad", "Feche o notepad"),
    ("Ligue as luzes da sala", "Desligue as luzes da sala"),
    ("Aumente o volume", "Diminua o volume"),
    ("Pause a música", "Pule para a próxima faixa"),
    ("Abra o Spotify", "Abra o navegador"),
])
def test_opposites_and_targets_never_become_equivalent(left: str, right: str) -> None:
    assert not preserved(parse(left), parse(right))["semantic"]


def test_numbers_remain_distinct_and_digits_are_safe_equivalent() -> None:
    five = parse("Defina um timer de cinco minutos")
    assert five.parameters == {"minutes": 5}
    assert preserved(five, parse("Defina um timer de 5 minutos"))["semantic"]
    for other in ("quinze", "cinquenta", "15", "50"):
        assert not preserved(five, parse(f"Defina um timer de {other} minutos"))["semantic"]


def test_courtesy_is_semantically_neutral_but_negation_is_not(catalog: Catalog) -> None:
    assert parse("Diminua o volume") == parse("DIMINUA O VOLUME, por favor!")
    assert parse("Não abra o notepad").negated
    controlled = with_desktop_control(catalog)
    assert resolve("Abra o notepad", controlled).outcome == "confirm"
    assert resolve("Feche o notepad", controlled).outcome == "confirm"
    assert resolve("Não abra o notepad", controlled).outcome == "reject"
    assert resolve("Abra o notepad e feche o calculator", controlled).outcome == "reject"
    assert resolve("Abra C:\\Windows\\System32\\cmd.exe", controlled).outcome == "reject"


def test_catalog_is_from_registration_and_effective_config(catalog: Catalog) -> None:
    assert catalog.active("get_datetime")
    assert not catalog.active("desktop_open_application")
    assert not catalog.active("browser_new_tab")
    assert not catalog.active("local_media_control")
    assert not catalog.active("local_volume_control")
    assert not any("light" in name or "timer" in name for name in catalog.tools)
    assert catalog.applications == {
        "notepad", "calculator", "explorer", "spotify", "obsidian",
        "default_browser",
    }
    assert resolve("Que horas são?", catalog).outcome == "execute"
    assert resolve("Que horas não são?", catalog).outcome == "reject"
    assert resolve("Abra o Spotify", catalog).outcome == "reject"


def test_phonetic_hints_never_authorize_an_action(catalog: Catalog) -> None:
    controlled = with_desktop_control(catalog)
    result = resolve("Abrra o notepad", controlled)
    assert result.outcome == "reject"
    assert result.tool is None
    assert resolve("Qual a diferença entre sessão e sessão?", catalog).outcome == "reject"
