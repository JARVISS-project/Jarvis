from jarvis.skills.matcher import SkillMatcher
from jarvis.skills.models import SkillDefinition, SkillStep


def test_matcher_reuses_only_exact_normalized_trigger() -> None:
    matcher = SkillMatcher()
    skill = SkillDefinition(
        name="Abrir nota",
        original_request="Abra a nota Teste e leia ela.",
        triggers=("Abra a nota Teste e leia ela.",),
        steps=(SkillStep("obsidian_read_note", {"path": "Teste.md"}),),
    )

    assert matcher.match("abra a nota teste e leia ela", [skill]) is skill
    assert matcher.match("abra a nota outra e leia ela", [skill]) is None


def test_matcher_fails_closed_when_two_skills_share_trigger() -> None:
    matcher = SkillMatcher()
    first = SkillDefinition(
        name="A",
        original_request="faça isso",
        steps=(SkillStep("tool_a", {}),),
    )
    second = SkillDefinition(
        name="B",
        original_request="faça isso",
        steps=(SkillStep("tool_b", {}),),
    )

    assert matcher.match("faça isso", [first, second]) is None


def test_explicit_skill_name_tolerates_small_stt_variation_only_when_unique() -> None:
    matcher = SkillMatcher()
    skill = SkillDefinition(
        name="Ler Jarvis",
        steps=(SkillStep("read_file", {"path": "a.txt"}),),
    )

    assert matcher.match_explicit_name("Lê Jarvis", [skill]) is skill


def test_explicit_skill_name_rejects_weak_fuzzy_guess() -> None:
    matcher = SkillMatcher()
    skill = SkillDefinition(
        name="Abrir Spotify",
        steps=(SkillStep("read_file", {"path": "a.txt"}),),
    )

    assert matcher.match_explicit_name("Lê Jarvis", [skill]) is None
