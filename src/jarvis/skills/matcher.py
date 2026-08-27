from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata

from jarvis.skills.models import SkillDefinition


class SkillMatcher:
    """Conservative trigger matcher for learned procedures.

    V1 intentionally uses normalized exact matches only. This allows a repeated
    command to reuse a learned procedure without fuzzy guesses that could invoke
    the wrong action. Parameterized/generalized matching belongs to a later
    version with explicit schemas.
    """

    def match(
        self,
        message: str,
        skills: list[SkillDefinition],
    ) -> SkillDefinition | None:
        normalized = self.normalize(message)
        if not normalized:
            return None
        matches = []
        for skill in skills:
            triggers = [*skill.triggers]
            if skill.original_request:
                triggers.append(skill.original_request)
            if any(self.normalize(trigger) == normalized for trigger in triggers):
                matches.append(skill)
        return matches[0] if len(matches) == 1 else None


    def match_explicit_name(
        self,
        identifier: str,
        skills: list[SkillDefinition],
        *,
        minimum_score: float = 0.90,
        minimum_margin: float = 0.05,
    ) -> SkillDefinition | None:
        """Resolve a Skill named explicitly by the user.

        Exact normalized names win. A small fuzzy fallback exists only for an
        explicit "execute a Skill ..." command, where STT may turn ``ler``
        into ``lê``. Automatic trigger matching remains exact and conservative.
        """
        normalized = self.normalize(identifier)
        if not normalized:
            return None

        exact = [
            skill for skill in skills
            if self.normalize(skill.name) == normalized
            or self.normalize(skill.id) == normalized
        ]
        if len(exact) == 1:
            return exact[0]
        if exact:
            return None

        ranked: list[tuple[float, SkillDefinition]] = []
        for skill in skills:
            score = SequenceMatcher(
                None, normalized, self.normalize(skill.name)
            ).ratio()
            ranked.append((score, skill))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked or ranked[0][0] < minimum_score:
            return None
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < minimum_margin:
            return None
        return ranked[0][1]

    @staticmethod
    def normalize(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        plain = "".join(
            char for char in decomposed if not unicodedata.combining(char)
        )
        return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())
