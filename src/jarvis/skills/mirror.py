from __future__ import annotations

from pathlib import Path
import re
import unicodedata

from jarvis.integrations.obsidian.vault import ObsidianVault
from jarvis.skills.models import SkillDefinition


class ObsidianSkillMirror:
    """Human-readable mirror of executable skills.

    Markdown is never parsed back into executable steps. This makes the graph
    useful without turning arbitrary note content into authority.
    """

    def __init__(self, vault: ObsidianVault, directory: str = "Jarvis/Skills") -> None:
        self.vault = vault
        self.directory = directory.strip("/\\") or "Jarvis/Skills"

    def sync(self, skill: SkillDefinition, *, active: bool = True) -> str:
        path = f"{self.directory}/{self._safe_filename(skill.name)}.md"
        self.vault.write_note(path, self._render(skill, active=active))
        return path

    def _render(self, skill: SkillDefinition, *, active: bool) -> str:
        status = "ativa" if active else "arquivada"
        lines = [
            f"# {skill.name}",
            "",
            "> Espelho legível da Skill. Esta nota NÃO é fonte executável.",
            "",
            f"- ID: `{skill.id}`",
            f"- Status: **{status}**",
            f"- Criada em: `{skill.created_at}`",
            f"- Execuções: {skill.execution_count}",
            f"- Sucessos: {skill.success_count}",
            "- Hub: [[Jarvis Skills]]",
            "",
            "## Objetivo",
            skill.description or "Procedimento reutilizável aprendido pelo Jarvis.",
            "",
            "## Pedido que originou a Skill",
            skill.original_request or "Não registrado.",
            "",
            "## Procedimento",
        ]
        for index, step in enumerate(skill.steps, 1):
            lines.append(
                f"{index}. [[Tool - {step.tool_name}]] — argumentos registrados: "
                f"`{step.arguments}`"
            )
        lines.extend(
            [
                "",
                "## Segurança",
                "A Skill lembra **como** executar. A autorização continua sendo "
                "decidida pela Policy/SafetyManager em cada execução.",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _safe_filename(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value)
        plain = "".join(char for char in decomposed if not unicodedata.combining(char))
        safe = re.sub(r"[<>:\"/\\|?*]+", "-", plain).strip(" .-")
        return safe[:100] or "Skill"
