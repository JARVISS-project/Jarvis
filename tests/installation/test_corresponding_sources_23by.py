from io import BytesIO
import hashlib

import pytest

from scripts.prepare_windows_sources import acquire, recipe_sources


def test_build_recipe_is_parsed_without_executing_python(tmp_path):
    marker = tmp_path / 'must-not-exist'
    source = f"open({str(marker)!r}, 'w').write('unsafe')\nPackage(name='codec', source_url='http://example.org/code.tar.gz', sha256='abc')"
    entries = recipe_sources(source)
    assert not marker.exists()
    assert entries == [{'filename': 'codec-code.tar.gz', 'url': 'https://example.org/code.tar.gz', 'sha256': 'abc'}]


@pytest.mark.parametrize('filename', ['../outside.tar.gz', '/outside.tar.gz'])
def test_source_destination_cannot_escape_output(tmp_path, filename):
    with pytest.raises(ValueError):
        acquire({'filename': filename, 'url': 'https://example.org/source'}, tmp_path)


def test_corrupt_download_never_replaces_a_verified_archive(tmp_path, monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: BytesIO(b'corrupt'))
    with pytest.raises(ValueError, match='hash mismatch'):
        acquire({'filename': 'source.tar.gz', 'url': 'https://example.org/source', 'sha256': '0'*64}, tmp_path)
    assert not (tmp_path/'source.tar.gz').exists()


def test_verified_local_source_is_reused_without_network(tmp_path, monkeypatch):
    content = b'corresponding source'
    (tmp_path/'source.tar.gz').write_bytes(content)
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: pytest.fail('network'))
    result = acquire({'filename': 'source.tar.gz', 'url': 'https://example.org/source',
                      'sha256': hashlib.sha256(content).hexdigest()}, tmp_path)
    assert result['bytes'] == len(content)
