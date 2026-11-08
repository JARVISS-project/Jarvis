"""Offline catalogue integrity and adversarial language tests."""

from __future__ import annotations

from copy import deepcopy
import json

import pytest

from scripts.capability_catalog_23ad import CATALOG, ROOT, decide_text, inventory, validate


@pytest.fixture(scope="module")
def catalog() -> dict:
    result = inventory()
    validate(result)
    return result


def test_frozen_catalog_matches_implementations(catalog: dict) -> None:
    assert json.loads(CATALOG.read_text(encoding="utf-8")) == catalog
    assert len(catalog["capabilities"]) == 61
    assert {'organization_execute', 'organization_undo'} <= {item['id'] for item in catalog['capabilities']}
    for item in catalog['capabilities']:
        if item['id'].startswith('organization_'):
            assert item['targets']['kind'] == 'sealed_plan_selected_root_rules'
            assert item['confirmation'] == 'required_by_policy'
            assert 'default ask' in item['runtime_confirmation']
    assert {'app_catalog_open', 'app_catalog_status', 'app_catalog_window', 'app_catalog_close'} <= {item['id'] for item in catalog['capabilities']}
    assert {'google_drive_find_files', 'google_drive_select_file', 'google_drive_metadata',
            'google_docs_read'} <= {item['id'] for item in catalog['capabilities']}
    assert all(item['network'] for item in catalog['capabilities']
               if item['id'].startswith(('google_drive_', 'google_docs_')))
    identifiers = {row["id"] for row in catalog["capabilities"]}
    assert "google_calendar_list_events" in identifiers
    assert "google_calendar_get_event" not in identifiers
    assert "google_calendar_check_availability" not in identifiers
    assert len([row for row in catalog["capabilities"]
                if row["availability"] == "enabled_by_config"]) == 10
    assert all((ROOT / row["implementation"]).exists() for row in catalog["capabilities"])


@pytest.mark.parametrize("mutation,match", [
    (lambda data: data["capabilities"].append(deepcopy(data["capabilities"][0])), "duplicate"),
    (lambda data: data["capabilities"][1]["aliases"].append(data["capabilities"][0]["id"]), "alias"),
    (lambda data: data["capabilities"][0].pop("risk"), "risk"),
    (lambda data: data["capabilities"][0]["parameters"]["properties"].update(
        {"bad": {}}), "type"),
    (lambda data: data["language"]["opposites"].pop("close"), "opposite"),
    (lambda data: next(row for row in data["capabilities"]
                       if row["availability"] == "enabled_by_config").update(
                           implementation_proven=False), "implementation"),
    (lambda data: next(row for row in data["capabilities"]
                       if row["id"] == "desktop_open_application")["targets"]["aliases"].update(
                           {"impostor": "not_in_catalog"}), "target alias"),
    (lambda data: data["source_sha256"].update({"src/jarvis/config.py": "0" * 64}),
     "source drift"),
])
def test_invalid_catalog_fails_closed(catalog: dict, mutation, match: str) -> None:
    bad = deepcopy(catalog)
    mutation(bad)
    with pytest.raises(ValueError, match=match):
        validate(bad)


@pytest.mark.parametrize("text", [
    "Abra o notepad", "Feche o notepad", "Ligue as luzes da sala",
    "Desligue as luzes da sala", "Aumente o volume", "Diminua o volume",
    "Pause a música", "Pule para a próxima faixa", "Não abra o notepad",
    "Defina um timer de cinco minutos", "Defina um timer de quinze minutos",
    "Defina um timer de cinquenta minutos", "Abra o Spotfy", "Abra o navegador",
    "Abra", "Defina um timer de minutos", "Abra o notepad e feche o calculator",
    "Abra o Spotify", "Abrra o notepad", "Não desligue as luzes",
])
def test_adversarial_commands_never_execute_directly(catalog: dict, text: str) -> None:
    assert decide_text(text, catalog) == "reject"


def test_only_exact_datetime_read_is_candidate(catalog: dict) -> None:
    assert decide_text("Que horas são?", catalog) == "diagnostic_read_candidate"
    assert decide_text("Que horas não são?", catalog) == "reject"
    assert decide_text("Que horas são e apague um arquivo", catalog) == "reject"


def test_real_application_alias_can_only_request_confirmation(catalog: dict) -> None:
    controlled = deepcopy(catalog)
    row = next(row for row in controlled["capabilities"]
               if row["id"] == "desktop_open_application")
    row["availability"] = "enabled_by_config"
    assert decide_text("Abra o bloco de notas", controlled) == "confirm"
    assert decide_text("Abra o noteped", controlled) == "reject"
    assert decide_text("Não abra o bloco de notas", controlled) == "reject"


def test_json_schema_is_parseable_and_matches_version(catalog: dict) -> None:
    schema = json.loads((ROOT / "docs/sprint-23ad-capability-catalog.schema.json")
                        .read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == catalog["schema_version"]
