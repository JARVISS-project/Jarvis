from pathlib import Path


def test_repository_guidelines_and_state_document_exist() -> None:
    assert Path("AGENTS.md").is_file()
    assert Path("docs/JARVIS_STATE.md").is_file()


def test_local_patch_artifacts_are_ignored() -> None:
    patterns = set(Path(".gitignore").read_text(encoding="utf-8").splitlines())
    assert "/*.patch" in patterns
    assert "/JARVIS_STATE.md" in patterns
    assert "/jarvis_state.txt" in patterns
