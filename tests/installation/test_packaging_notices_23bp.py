from pathlib import Path

import pytest

from scripts.build_windows_23bo import license_destination


def test_distinct_wheel_notices_do_not_overwrite_each_other(tmp_path):
    first = license_destination(tmp_path, 'library', Path('component_a/LICENSE'))
    second = license_destination(tmp_path, 'library', Path('component_b/LICENSE'))
    for path, content in ((first, b'first copyright'), (second, b'second copyright')):
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    assert first.read_bytes() == b'first copyright'
    assert second.read_bytes() == b'second copyright'


@pytest.mark.parametrize('relative', [Path('../LICENSE'), Path('/LICENSE')])
def test_notice_paths_cannot_escape_destination(tmp_path, relative):
    with pytest.raises(ValueError):
        license_destination(tmp_path, 'library', relative)
