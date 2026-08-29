import json
from pathlib import Path

import pytest

from jarvis.integrations.google.client import load_client_config
from jarvis.integrations.google.errors import GoogleClientConfigurationError


def valid_config() -> dict[str, object]:
    return {
        "installed": {
            "client_id": "example.apps.googleusercontent.com",
            "project_id": "jarvis-test",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": "not-rendered",
            "redirect_uris": ["http://localhost"],
        }
    }


def test_client_config_accepts_only_google_desktop_endpoints(tmp_path: Path) -> None:
    path = tmp_path / "client.json"
    path.write_text(json.dumps(valid_config()), encoding="utf-8")

    assert load_client_config(path)["installed"]


@pytest.mark.parametrize("mutation", ["web", "token_uri", "client_id"])
def test_client_config_rejects_wrong_type_or_endpoint(
    tmp_path: Path,
    mutation: str,
) -> None:
    config = valid_config()
    if mutation == "web":
        config = {"web": config["installed"]}
    elif mutation == "token_uri":
        config["installed"]["token_uri"] = "https://attacker.invalid/token"
    else:
        config["installed"]["client_id"] = "not-google"
    path = tmp_path / "client.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(GoogleClientConfigurationError) as captured:
        load_client_config(path)

    assert "not-rendered" not in str(captured.value)
