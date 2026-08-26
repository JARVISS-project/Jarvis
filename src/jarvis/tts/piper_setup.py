from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import urlopen


RAZO_MODEL_URL = (
    "https://huggingface.co/Lucasllfs/Razo-piper-voice/resolve/main/"
    "pt-BR-razo-medium.onnx?download=true"
)
RAZO_CONFIG_URL = (
    "https://huggingface.co/Lucasllfs/Razo-piper-voice/resolve/main/"
    "config.json?download=true"
)
RAZO_MODEL_SHA256 = (
    "91c815ae67edd7fbdd37925e3639a6c3dc5f57e58034223caac2a5ec5ecf548f"
)


def default_piper_model_path() -> Path:
    return Path("data/voices/razo/pt-BR-razo-medium.onnx")


def default_piper_config_path() -> Path:
    return Path(f"{default_piper_model_path()}.json")


def setup_piper_voice(
    model_path: Path | None = None,
    config_path: Path | None = None,
) -> tuple[Path, Path]:
    model = Path(model_path or default_piper_model_path())
    config = Path(config_path or default_piper_config_path())
    model.parent.mkdir(parents=True, exist_ok=True)
    config.parent.mkdir(parents=True, exist_ok=True)

    if not model.is_file() or _sha256(model) != RAZO_MODEL_SHA256:
        _download(RAZO_MODEL_URL, model)
        if _sha256(model) != RAZO_MODEL_SHA256:
            model.unlink(missing_ok=True)
            raise RuntimeError("Falha ao validar o modelo Piper baixado.")

    if not config.is_file():
        _download(RAZO_CONFIG_URL, config)

    return model, config


def _download(url: str, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    try:
        with urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        temporary.replace(destination)
    except Exception as error:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"Não foi possível baixar a voz rápida: {type(error).__name__}."
        ) from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
