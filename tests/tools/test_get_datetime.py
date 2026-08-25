from datetime import datetime, timedelta, timezone

from jarvis.security.risk import RiskLevel
from jarvis.tools.get_datetime import GetDateTimeTool


def test_get_datetime_is_safe_and_uses_injected_clock() -> None:
    expected = datetime(
        2026,
        8,
        25,
        1,
        3,
        tzinfo=timezone(timedelta(hours=-3)),
    )
    tool = GetDateTimeTool(clock=lambda: expected)

    result = tool.execute()

    assert tool.risk is RiskLevel.SAFE
    assert result == (
        "Data: 25/08/2026\n"
        "Hora: 01:03\n"
        "Timezone: -03:00\n"
        "ISO: 2026-08-25T01:03:00-03:00"
    )


def test_get_datetime_has_no_arguments_or_shell_target() -> None:
    tool = GetDateTimeTool()

    assert tool.parameters["properties"] == {}
    assert tool.get_target() == "relógio local"
    assert "shell" not in tool.description.casefold()
