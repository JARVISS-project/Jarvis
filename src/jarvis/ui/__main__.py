"""Entrada da Ambient UI, em modo demo ou conectada ao runtime."""

import argparse
from pathlib import Path

from jarvis.ui.launcher import run_ambient_ui_demo, run_ambient_ui_live


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m jarvis.ui")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Conecta ao Voice Runtime em vez de usar estados simulados.",
    )
    parser.add_argument(
        "--endpoint",
        type=Path,
        default=Path("data/runtime/ambient-ui.json"),
        help="Arquivo local com o endpoint do Runtime State Bus.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _argument_parser().parse_args(argv)
    try:
        result = (
            run_ambient_ui_live(args.endpoint)
            if args.live
            else run_ambient_ui_demo()
        )
        raise SystemExit(result)
    except RuntimeError as error:
        raise SystemExit(f"Erro ao iniciar Ambient UI: {error}") from error


if __name__ == "__main__":
    main()
