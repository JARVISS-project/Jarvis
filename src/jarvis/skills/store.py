from __future__ import annotations

from abc import ABC, abstractmethod
import json
from pathlib import Path

from jarvis.skills.models import SkillDefinition


class SkillStore(ABC):
    @abstractmethod
    def list(self) -> list[SkillDefinition]: ...

    @abstractmethod
    def get(self, identifier: str) -> SkillDefinition | None: ...

    @abstractmethod
    def save(self, skill: SkillDefinition) -> None: ...

    @abstractmethod
    def delete(self, identifier: str) -> SkillDefinition | None: ...


class InMemorySkillStore(SkillStore):
    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def list(self) -> list[SkillDefinition]:
        return sorted(self._skills.values(), key=lambda item: item.name.casefold())

    def get(self, identifier: str) -> SkillDefinition | None:
        normalized = identifier.strip().casefold()
        for skill in self._skills.values():
            if skill.id.casefold() == normalized or skill.name.casefold() == normalized:
                return skill
        return None

    def save(self, skill: SkillDefinition) -> None:
        self._skills[skill.id] = skill

    def delete(self, identifier: str) -> SkillDefinition | None:
        skill = self.get(identifier)
        if skill is None:
            return None
        return self._skills.pop(skill.id, None)


class JsonSkillStore(SkillStore):
    """Local executable source of truth for learned skills.

    Obsidian is deliberately not read here. Markdown mirrors are human-readable
    documentation only and can never grant execution capability.
    """

    VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def list(self) -> list[SkillDefinition]:
        return sorted(self._load().values(), key=lambda item: item.name.casefold())

    def get(self, identifier: str) -> SkillDefinition | None:
        normalized = identifier.strip().casefold()
        for skill in self._load().values():
            if skill.id.casefold() == normalized or skill.name.casefold() == normalized:
                return skill
        return None

    def save(self, skill: SkillDefinition) -> None:
        skills = self._load()
        skills[skill.id] = skill
        self._write(skills)

    def delete(self, identifier: str) -> SkillDefinition | None:
        skills = self._load()
        normalized = identifier.strip().casefold()
        selected = next(
            (
                skill
                for skill in skills.values()
                if skill.id.casefold() == normalized
                or skill.name.casefold() == normalized
            ),
            None,
        )
        if selected is None:
            return None
        skills.pop(selected.id, None)
        self._write(skills)
        return selected

    def _load(self) -> dict[str, SkillDefinition]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Arquivo local de Skills inválido.") from error
        if not isinstance(raw, dict) or raw.get("version") != self.VERSION:
            raise RuntimeError("Formato local de Skills não suportado.")
        entries = raw.get("skills", [])
        if not isinstance(entries, list):
            raise RuntimeError("Arquivo local de Skills inválido.")
        result: dict[str, SkillDefinition] = {}
        try:
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError
                skill = SkillDefinition.from_dict(entry)
                result[skill.id] = skill
        except ValueError as error:
            raise RuntimeError("Arquivo local de Skills inválido.") from error
        return result

    def _write(self, skills: dict[str, SkillDefinition]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "skills": [skill.to_dict() for skill in skills.values()],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
