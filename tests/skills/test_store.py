from pathlib import Path

from jarvis.skills.models import SkillDefinition, SkillStep
from jarvis.skills.store import JsonSkillStore


def test_json_skill_store_round_trip(tmp_path: Path) -> None:
    store = JsonSkillStore(tmp_path / "skills.json")
    skill = SkillDefinition(
        name="Ler nota",
        original_request="Abra Teste e leia",
        triggers=("Abra Teste e leia",),
        steps=(SkillStep("obsidian_read_note", {"path": "Teste.md"}),),
    )

    store.save(skill)

    loaded = store.get(skill.id)
    assert loaded is not None
    assert loaded.name == "Ler nota"
    assert loaded.steps[0].arguments == {"path": "Teste.md"}
    assert store.get("ler nota") is not None


def test_json_skill_store_delete_by_name(tmp_path: Path) -> None:
    store = JsonSkillStore(tmp_path / "skills.json")
    skill = SkillDefinition(
        name="Abrir nota",
        steps=(SkillStep("obsidian_open_note", {"path": "Teste.md"}),),
    )
    store.save(skill)

    removed = store.delete("Abrir nota")

    assert removed is not None
    assert removed.id == skill.id
    assert store.list() == []
