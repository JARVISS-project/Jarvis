"""Offline regression against the advisories reviewed for the Windows profile."""
import json
from pathlib import Path
import tomllib
from urllib.parse import unquote, urlparse

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / 'requirements/windows-py313'
ADVISORIES = json.loads((Path(__file__).parent / 'fixtures/dependency_advisories_2026_11_10.json').read_text(encoding='utf-8'))


def pinned(line):
    dependency = Requirement(line.split(' --hash=')[0])
    if dependency.url:
        version = parse_wheel_filename(Path(unquote(urlparse(dependency.url).path)).name)[1]
    else:
        version = str(dependency.specifier).removeprefix('==')
    return canonicalize_name(dependency.name), version


def test_no_pinned_profile_dependency_matches_reviewed_advisories():
    findings = []
    for file in [*PROFILE.glob('*.lock'), PROFILE / 'constraints.txt']:
        for line in file.read_text(encoding='utf-8').splitlines():
            if not line or line.startswith('#'):
                continue
            name, version = pinned(line)
            for advisory in ADVISORIES:
                if name == advisory['package'] and SpecifierSet(advisory['affected']).contains(version):
                    findings.append(f"{file.name}: {name} {version}: {advisory['ghsa']}")
    assert not findings, '\n'.join(findings)


def test_product_extras_and_build_metadata_reject_known_affected_versions():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    for group in ('voice', 'neural-voice'):
        requirements = {canonicalize_name(r.name): r for r in map(Requirement, project['project']['optional-dependencies'][group])}
        for name, versions in [('torch', ['2.6.0', '2.10.0', '2.12.1']),
                               ('transformers', ['5.2.0', '5.8.1', '5.9.0'])]:
            assert all(not requirements[name].specifier.contains(v) for v in versions)
    builder = next(r for r in map(Requirement, project['build-system']['requires']) if r.name == 'setuptools')
    assert not builder.specifier.contains('84.0.0')


def test_review_covers_original_checkpoint_and_remote_code_alerts():
    # A smaller or accidentally empty advisory fixture must not erase the gate.
    ids = {advisory['ghsa'] for advisory in ADVISORIES}
    assert {'GHSA-63cw-57p8-fm3p', 'GHSA-x9r9-c232-4q39', 'GHSA-xmj3-9gf7-h52w'} <= ids
    assert len(ids) == 14
