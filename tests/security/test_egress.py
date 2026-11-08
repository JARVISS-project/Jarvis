from pathlib import Path

import pytest

from jarvis.security.egress import (
    DataClassification,
    DownloadArtifact,
    EgressPolicy,
)


@pytest.mark.parametrize(
    "classification",
    [
        DataClassification.UNKNOWN,
        DataClassification.SECRET,
        DataClassification.RESTRICTED,
    ],
)
def test_sensitive_or_unknown_egress_fails_closed(classification) -> None:
    policy = EgressPolicy(allowed_hosts={"example.com"})

    with pytest.raises(PermissionError):
        policy.authorize(
            "https://example.com/upload",
            purpose="test",
            classification=classification,
        )


def test_destination_and_purpose_are_bound() -> None:
    policy = EgressPolicy(allowed_hosts={"example.com"})

    permit = policy.authorize(
        "https://sub.example.com/resource",
        purpose="public-download",
        classification=DataClassification.PUBLIC,
    )

    assert permit.destination == "https://sub.example.com/resource"
    assert permit.purpose == "public-download"

    with pytest.raises(PermissionError):
        policy.authorize(
            "https://attacker.example/resource",
            purpose="public-download",
            classification=DataClassification.PUBLIC,
        )


def test_bounded_retry_is_audited_and_verified() -> None:
    events: list[tuple[str, str, str]] = []
    policy = EgressPolicy(
        allowed_hosts={"example.com"},
        audit_sink=lambda *event: events.append(event),
    )
    permit = policy.authorize(
        "https://example.com/resource",
        purpose="download",
        classification=DataClassification.PUBLIC,
    )
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary")
        return "verified"

    result = policy.run_bounded(
        permit,
        operation,
        max_attempts=2,
        verify=lambda value: value == "verified",
    )

    assert result == "verified"
    assert calls == 2
    statuses = [event[2] for event in events]
    assert "attempt:1" in statuses
    assert "failed:1" in statuses
    assert "attempt:2" in statuses
    assert "verified" in statuses


def test_retry_count_is_strictly_bounded() -> None:
    policy = EgressPolicy()
    permit = policy.authorize(
        "https://example.com/resource",
        purpose="test",
        classification=DataClassification.PUBLIC,
    )
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        raise OSError("always")

    with pytest.raises(OSError):
        policy.run_bounded(permit, operation, max_attempts=2)

    assert calls == 2


def test_download_artifact_is_untrusted_by_default(tmp_path: Path) -> None:
    artifact = DownloadArtifact(
        tmp_path / "download.bin",
        "https://example.com/download.bin",
    )

    assert artifact.trust == "untrusted"
