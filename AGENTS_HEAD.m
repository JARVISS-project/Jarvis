# Repository Guidelines

## Project Structure & Module Organization

Jarvis is a Python 3.11+ package using a `src` layout. Application code lives in `src/jarvis/`: `llm/` contains model client abstractions and Ollama integration, `tools/` defines and registers executable tools, and `security/` handles policy, filesystem boundaries, confirmation, risk, and auditing. The CLI entry point is `src/jarvis/__main__.py`, while `harness.py` coordinates the LLM and tool execution. Put automated tests in `tests/`, mirroring package paths (for example, `tests/security/test_policy.py`). Treat `logs/` as runtime output, not source.

## Build, Test, and Development Commands

- `python -m venv .venv` creates a local virtual environment.
- `.\.venv\Scripts\Activate.ps1` activates it in PowerShell.
- `python -m pip install -e .` installs Jarvis and its Ollama dependency in editable mode.
- `jarvis` (or `python -m jarvis`) starts the assistant; ensure a local Ollama service and required model are available.
- `python -m pytest` runs the test suite configured in `pyproject.toml`.
- `python -m build` creates distribution artifacts when the optional `build` package is installed.

## Coding Style & Naming Conventions

Follow standard PEP 8 conventions: four-space indentation, imports grouped at the top, and type annotations on public functions and constructors. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Keep tool implementations focused and derive them from `Tool`. No formatter or linter is currently configured, so match neighboring code and keep changes small.

## Testing Guidelines

Use pytest and name files `test_*.py` and test functions `test_<behavior>`. Add unit tests for new policy decisions, argument validation, and tool behavior. Security tests should cover both allowed and denied paths, especially filesystem escape attempts and confirmation requirements. Avoid calling a live Ollama service in unit tests; substitute a fake `LLMClient` such as the existing echo implementation where suitable.

## Commit & Pull Request Guidelines

Recent commits use short, imperative Portuguese summaries such as `Adiciona camada de seguranca...`. Keep each commit focused and follow that style. Pull requests should explain the motivation and behavior change, list verification commands, link relevant issues, and highlight security or configuration effects. Include terminal output or screenshots only when CLI-visible behavior changes.

## Security & Configuration

Never commit `.env`, credentials, local model data, virtual environments, or audit logs. Route filesystem operations through `FileSystemGuard` and tool execution through the existing safety and audit layers; do not bypass confirmation or policy checks.
