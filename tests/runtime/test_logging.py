import logging
from pathlib import Path

from jarvis.runtime.logging import configure_runtime_logging


def test_runtime_logging_rotates_and_redacts_secrets(tmp_path: Path) -> None:
    path = tmp_path / "runtime.log"
    handler = configure_runtime_logging(path, max_bytes=160, backup_count=1)
    logger = logging.getLogger("jarvis.runtime")
    logger.info("token=top-secret authorization: Bearer abc123")
    handler.flush()
    redacted = path.read_text(encoding="utf-8")
    assert "top-secret" not in redacted
    assert "abc123" not in redacted
    assert "[REDACTED]" in redacted
    for index in range(20):
        logger.info("technical event %s", index)
    handler.flush()

    content = "".join(
        file.read_text(encoding="utf-8") for file in tmp_path.glob("runtime.log*")
    )
    assert "top-secret" not in content
    assert "abc123" not in content
    assert (tmp_path / "runtime.log.1").exists()


def test_runtime_logging_redacts_json_and_exception_text(tmp_path: Path) -> None:
    path = tmp_path / "runtime.log"
    handler = configure_runtime_logging(path)
    logger = logging.getLogger("jarvis.runtime")
    try:
        raise RuntimeError('{"access_token":"json-secret"}')
    except RuntimeError:
        logger.exception("pairing_code=654321")
    handler.flush()

    content = path.read_text(encoding="utf-8")
    assert "json-secret" not in content
    assert "654321" not in content
    assert content.count("[REDACTED]") >= 2


def test_runtime_logging_redacts_google_oauth_secrets(tmp_path: Path) -> None:
    path = tmp_path / "runtime.log"
    handler = configure_runtime_logging(path)
    logger = logging.getLogger("jarvis.runtime")
    logger.error(
        "client_secret=client-value authorization_code=code-value "
        "refresh_token=refresh-value"
    )
    handler.flush()

    content = path.read_text(encoding="utf-8")
    assert "client-value" not in content
    assert "code-value" not in content
    assert "refresh-value" not in content
