from pathlib import Path

from jarvis.tts import piper_setup


def test_setup_piper_voice_downloads_missing_files(
    tmp_path: Path, monkeypatch
) -> None:
    model = tmp_path / "voice.onnx"
    config = tmp_path / "voice.onnx.json"
    calls: list[tuple[str, Path]] = []

    def fake_download(url: str, destination: Path) -> None:
        calls.append((url, destination))
        destination.write_bytes(b"model" if destination == model else b"{}")

    monkeypatch.setattr(piper_setup, "_download", fake_download)
    monkeypatch.setattr(
        piper_setup,
        "_sha256",
        lambda path: piper_setup.RAZO_MODEL_SHA256 if path == model else "unused",
    )

    result = piper_setup.setup_piper_voice(model, config)

    assert result == (model, config)
    assert calls == [
        (piper_setup.RAZO_MODEL_URL, model),
        (piper_setup.RAZO_CONFIG_URL, config),
    ]
