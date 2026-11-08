"""Offline focused tests for reproduction scripts; no models or live services."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts/reproduction" / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hash_and_match(tmp_path):
    path = tmp_path / "model.bin"
    path.write_bytes(b"test-artifact")
    verifier = load("verify_models")
    expected = hashlib.sha256(b"test-artifact").hexdigest()
    assert verifier.sha256(path) == expected
    assert verifier.verify_files(tmp_path, {"model.bin": expected})["model.bin"]["status"] == "match"


def test_missing_and_mismatch(tmp_path):
    (tmp_path / "model.bin").write_bytes(b"wrong")
    report = load("verify_models").verify_files(tmp_path, {"model.bin": "0"*64, "absent": "0"*64})
    assert report["model.bin"]["status"] == "mismatch"
    assert report["absent"]["status"] == "missing"


@pytest.mark.parametrize("name", ["../escape", "../../escape", str(ROOT / "absolute")])
def test_manifest_path_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        load("verify_models").verify_files(tmp_path, {name: "0"*64})


@pytest.mark.parametrize("models,status", [
    ([], "missing"),
    ([{"name": "test", "digest": "bad"}], "mismatch"),
    ([{"name": "test", "digest": "sha256:expected"}], "match"),
])
def test_ollama_identity(models, status):
    result = load("verify_models").verify_ollama(models, {"tag": "test", "manifest_sha256": "expected"})
    assert result["status"] == status


def test_missing_module_fails(tmp_path):
    result = load("checks").run([sys.executable, "-I", "-c", "import deliberately_absent_23az"], tmp_path)
    assert result["exit"] != 0
    assert "ModuleNotFoundError" in result["stderr"]


def test_missing_executable_fails(tmp_path):
    result = load("checks").run([str(tmp_path / "absent.exe")], tmp_path)
    assert result["exit"] == 1


def test_inference_script_imports_under_isolation():
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/reproduction/infer_stt.py"), "--help"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0
    assert "--manifest" in result.stdout


@pytest.mark.parametrize("model,status", [("large-v3", "match"), ("medium", "mismatch")])
def test_runtime_selection_mismatch_is_visible(model, status):
    selected = {"stt": model, "wake": "hey_jarvis", "llm": "qwen"}
    manifest = {"stt": {"model_identifier": "large-v3"},
                "wake": {"model_identifier": "hey_jarvis"}, "qwen": {"tag": "qwen"}}
    assert load("verify_models").verify_selection(selected, manifest)["status"] == status


def test_wake_wrong_audio_blocked_before_model_load(tmp_path):
    import json
    wav = tmp_path / "wrong.wav"
    wav.write_bytes(b"not the public clip")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"public_wav": {"sha256": "0" * 64}}))
    result = subprocess.run(
        [sys.executable, "-I", str(ROOT / "scripts/reproduction/infer_wake.py"),
         "--wav", str(wav), "--manifest", str(manifest)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "blocked_wav_identity"


@pytest.mark.parametrize("user_site,venv,isolated,expected", [
    (False, True, 1, True), (True, True, 1, False),
    (False, False, 1, False), (False, True, 0, False),
])
def test_environment_requires_venv_no_user_site_and_isolation(user_site, venv, isolated, expected):
    assert load("checks").valid_environment(user_site, venv, isolated) == expected


@pytest.mark.parametrize("resources,status", [
    ({"error": "OpenProcess"}, "incomplete_resource_evidence"),
    ({"working_set_bytes": None, "peak_working_set_bytes": 1,
      "cuda_dlls": [{"isolated_venv": True}]}, "incomplete_resource_evidence"),
    ({"working_set_bytes": 1, "peak_working_set_bytes": 1,
      "cuda_dlls": []}, "incomplete_resource_evidence"),
    ({"working_set_bytes": 1, "peak_working_set_bytes": 1,
      "cuda_dlls": [{"isolated_venv": False}]}, "external_dll_dependency"),
    ({"working_set_bytes": 1, "peak_working_set_bytes": 1,
      "cuda_dlls": [{"isolated_venv": True}]}, "passed"),
])
def test_resource_evidence_required(resources, status):
    assert load("infer_stt").resource_status(resources) == status
