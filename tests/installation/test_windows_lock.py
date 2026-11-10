"""Offline contract checks for the platform-specific installation profile."""
from pathlib import Path
import re
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "requirements" / "windows-py313"
GROUPS = ("core", "voice", "ui", "media", "tests")


def entries(filename: str) -> dict[str, tuple[Requirement, str]]:
    result = {}
    for line in (PROFILE / filename).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        requirement, digest = line.split(" --hash=sha256:")
        parsed = Requirement(requirement)
        name = canonicalize_name(parsed.name)
        assert name not in result
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        assert parsed.url or str(parsed.specifier).startswith("==")
        result[name] = (parsed, digest)
    return result


@pytest.mark.parametrize("group", GROUPS)
def test_group_pins_and_hashes_match_union(group: str) -> None:
    union = entries("mvp.lock")
    selected = entries(group + ".lock")
    assert selected
    for name, (requirement, digest) in selected.items():
        full_requirement, full_digest = union[name]
        assert str(requirement) == str(full_requirement)
        assert digest == full_digest
    for line in (PROFILE / (group + ".in")).read_text().splitlines():
        if line and not line.startswith("#"):
            direct = Requirement(line)
            assert str(selected[canonicalize_name(direct.name)][0]) == str(direct)


def test_all_mvp_dependencies_belong_to_a_group() -> None:
    union = entries("mvp.lock")
    covered = set().union(*(entries(group + ".lock") for group in GROUPS))
    assert covered == set(union)
    assert len(union) == 80


def test_selected_versions_satisfy_product_metadata() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    requirements = list(project["dependencies"])
    for extra in ("voice", "fast-voice", "ui", "windows-media", "dev"):
        requirements.extend(project["optional-dependencies"][extra])
    union = entries("mvp.lock")
    for line in requirements:
        dependency = Requirement(line)
        selected = union[canonicalize_name(dependency.name)][0]
        if selected.url:
            raise AssertionError('The current profile uses hashed PyPI wheels, without a legacy CUDA URL.')
        else:
            version = str(selected.specifier).removeprefix("==")
            assert dependency.specifier.contains(version)


def test_bootstrap_is_pinned() -> None:
    bootstrap = entries("build.lock")
    assert set(bootstrap) == {"pip", "setuptools"}
    assert str(bootstrap["pip"][0].specifier) == "==26.1.2"
    assert str(bootstrap["setuptools"][0].specifier) == "==84.2.1"


def test_package_and_mvp_shared_dependencies_cannot_drift():
    packaged, mvp = entries('package.lock'), entries('mvp.lock')
    for name in packaged.keys() & mvp.keys():
        assert packaged[name] == mvp[name], name
