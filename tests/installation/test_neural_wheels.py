"""Synthetic upstream wheels: no network, package installation or model loading."""
import base64
import csv
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts.prepare_neural_wheels import (ALIGNMENT_OLD, build_variant, acquire,
                                           sha256, variant_content)


LOADER = '''class Model:
    @classmethod
    def from_local(cls, ckpt_dir, device):
        map_location = None
        ve = VoiceEncoder()
        t3 = T3(T3Config.multilingual())
        t3_state = load_safetensors(ckpt_dir / "t3_mtl23ls_v2.safetensors")
        t3.load_state_dict(t3_state)
        s3gen = S3Gen()
        return ve, t3, s3gen
'''


def upstream(tmp_path, package='chatterbox-tts', *, extra=None):
    version = '0.1.7' if package == 'chatterbox-tts' else '1.0.1'
    variant = version + ('+jarvis.4' if package == 'chatterbox-tts' else '+jarvis.2')
    stem = package.replace('-', '_') + '-' + version
    dist = stem + '.dist-info'
    files = {dist + '/METADATA': f'Name: {package}\nVersion: {version}\nRequires-Dist: torch==2.6.0\n\nOriginal description.\n'.encode(),
             dist + '/WHEEL': b'Wheel-Version: 1.0\nTag: py3-none-any\n',
             dist + '/licenses/LICENSE': b'Original upstream MIT copyright and license.',
             dist + '/RECORD': b'old records, replaced\n'}
    if package == 'chatterbox-tts':
        files.update({'chatterbox/mtl_tts.py': LOADER.encode(),
                      'chatterbox/models/t3/inference/alignment_stream_analyzer.py':
                          ('def detect(self, A):\n    ' + ALIGNMENT_OLD + '\n    return alignment_repetition\n').encode(),
                      'chatterbox/unmodified.py': b'# Original source unchanged.\n'})
    else:
        files.update({'perth/perth_net/__init__.py': b'from pkg_resources import resource_filename\nPREPACKAGED_MODELS_DIR = resource_filename(__name__, "pretrained")\n',
                      'perth/perth_net/pretrained/model.pt': b'synthetic watermark asset; never loaded'})
    files.update(extra or {})
    source = tmp_path / (stem + '-py3-none-any.whl')
    with zipfile.ZipFile(source, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return source, {'name': package, 'version': version, 'variant': variant, 'filename': source.name,
                    'sha256': sha256(source), 'url': 'https://files.pythonhosted.org/pinned.whl'}, files


@pytest.mark.parametrize('package', ['chatterbox-tts', 'resemble-perth'])
def test_variants_deterministic_preserve_licenses_assets_and_valid_records(tmp_path, package):
    source, recipe, original = upstream(tmp_path, package)
    before = source.read_bytes()
    first = build_variant(source, tmp_path / 'one', recipe)
    second = build_variant(source, tmp_path / 'two', recipe)
    assert first.read_bytes() == second.read_bytes()
    assert build_variant(source, first.parent, recipe) == first
    assert source.read_bytes() == before
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        metadata = archive.read(next(n for n in names if n.endswith('/METADATA')))
        assert ('Version: ' + recipe['variant']).encode() in metadata
        assert archive.read(next(n for n in names if n.endswith('/licenses/LICENSE'))) == next(v for n,v in original.items() if n.endswith('/licenses/LICENSE'))
        if package == 'chatterbox-tts':
            assert b'Requires-Dist: torch>=2.15,<3' in metadata
            assert b'torch==2.6.0' not in metadata
            assert archive.read('chatterbox/unmodified.py') == original['chatterbox/unmodified.py']
        else:
            assert archive.read('perth/perth_net/pretrained/model.pt') == original['perth/perth_net/pretrained/model.pt']
            assert b'pkg_resources' not in archive.read('perth/perth_net/__init__.py')
        record = next(n for n in names if n.endswith('/RECORD'))
        for name, digest, size in csv.reader(io.StringIO(archive.read(record).decode())):
            if name == record:
                assert not digest and not size
                continue
            data = archive.read(name)
            assert int(size) == len(data)
            assert digest == 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
        for name in names:
            if name.endswith('.py'):
                compile(archive.read(name), name, 'exec')


def test_hash_failure_and_existing_output_never_overwrite(tmp_path):
    source, recipe, _ = upstream(tmp_path)
    out = tmp_path / 'out'
    with pytest.raises(ValueError):
        build_variant(source, out, {**recipe, 'sha256': '0'*64})
    assert not out.exists()
    target = build_variant(source, out, recipe)
    target.write_bytes(b'preserve existing data')
    with pytest.raises(FileExistsError):
        build_variant(source, out, recipe)
    assert target.read_bytes() == b'preserve existing data'
    assert list(out.iterdir()) == [target]


@pytest.mark.parametrize('name', ['../escape.py', '/absolute.py', 'C:/escape.py', 'folder\\escape.py'])
def test_unsafe_members_refused_even_with_matching_input_hash(tmp_path, name):
    source, recipe, _ = upstream(tmp_path, extra={name: b'never extracted'})
    if '\\' in name:
        # zipfile normalizes Windows separators while writing. Preserve a truly
        # malformed original filename in both ZIP directory records instead.
        source.write_bytes(source.read_bytes().replace(name.replace('\\', '/').encode(), name.encode()))
        recipe['sha256'] = sha256(source)
    with pytest.raises(ValueError):
        build_variant(source, tmp_path / 'out', recipe)
    assert not (tmp_path / 'out').exists()


def test_changed_patch_source_is_refused_and_download_is_explicit(tmp_path):
    source, recipe, _ = upstream(tmp_path, extra={'chatterbox/mtl_tts.py': b'# Unexpected upstream revision.\n'})
    with pytest.raises(ValueError):
        build_variant(source, tmp_path / 'out', recipe)
    with pytest.raises(FileNotFoundError):
        acquire(tmp_path / 'missing', recipe, download=False)
    assert not (tmp_path / 'missing').exists()
    source.write_bytes(b'corrupt cached artifact')
    with pytest.raises(ValueError):
        acquire(tmp_path, recipe, download=True)
    assert source.read_bytes() == b'corrupt cached artifact'


def test_short_alignment_cannot_reduce_an_empty_dimension(tmp_path):
    source, recipe, _ = upstream(tmp_path)
    files = variant_content(source, recipe)
    namespace = {}
    exec(files['chatterbox/models/t3/inference/alignment_stream_analyzer.py'], namespace)
    class Array:
        def __init__(self, width): self.width = width
        def size(self, dimension): assert dimension == 1; return self.width
        def __getitem__(self, key): assert self.width > 5; return self
        def max(self, dim): return SimpleNamespace(values=SimpleNamespace(sum=lambda: 6))
    detector = SimpleNamespace(complete=True, completed_at=0)
    assert namespace['detect'](detector, Array(3)) is False
    assert namespace['detect'](detector, Array(12)) is True
