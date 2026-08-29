from pathlib import Path


REMOTE_PATH = Path(__file__).parents[2] / "src" / "jarvis" / "remote"


def test_gateway_side_has_no_tool_execution_or_artificial_grant_path() -> None:
    gateway_side = [
        "gateway.py",
        "bridge.py",
        "auth.py",
        "protocol.py",
        "sessions.py",
        "transport.py",
        "websocket_transport.py",
    ]
    source = "\n".join(
        (REMOTE_PATH / filename).read_text(encoding="utf-8")
        for filename in gateway_side
    )

    assert "ToolExecutor" not in source
    assert "ToolRegistry" not in source
    assert "grant_factory" not in source
    assert "grant_explicit" not in source
    assert "jarvis.harness" not in source


def test_harness_is_referenced_only_by_the_core_port_adapter() -> None:
    references = []
    for path in REMOTE_PATH.glob("*.py"):
        if "jarvis.harness" in path.read_text(encoding="utf-8"):
            references.append(path.name)

    assert references == ["core_port.py"]
