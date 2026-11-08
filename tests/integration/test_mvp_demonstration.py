from pathlib import Path

import pytest

from scripts.run_mvp_demonstration import run


def test_safe_mvp_demonstration_observes_all_steps(tmp_path: Path) -> None:
    output = tmp_path / "results.json"
    payload = run(output, tmp_path / "work")
    assert payload["counts"] == {"passed": 46, "total": 46}
    assert payload["zero_child_residual"] is True
    assert all(step["passed"] for step in payload["steps"])
    assert output.is_file()
    assert payload['external_network_calls'] == 0
    assert payload['prior_real_validation']['repeated'] is False
    steps = {s['name']: s for s in payload['steps']}
    for name in ('confirmação expirada', 'permissão revogada', 'delegação cancelada',
                 'delegação expirada', 'delegação revogada', 'delegação bloqueada',
                 'memória bloqueada', 'leitura bloqueada', 'organização replay',
                 'rotina cancelada', 'rotina expirada', 'rotina revogada', 'rotina bloqueada',
                 'organização cancelada', 'organização expirada', 'organização revogada', 'organização bloqueada'):
        assert steps[name]['execution_count'] == 0
    for name in ('delegação confirmada offline', 'rotina executada', 'organização executada'):
        assert steps[name]['execution_count'] == 1
    assert steps['organização executada']['evidence'] == 'temporary-files-real'
    assert steps['delegação confirmada offline']['evidence'] == 'simulated'


def test_demo_refuses_existing_files_without_modifying_them(tmp_path: Path) -> None:
    marker = tmp_path / 'preserve.txt'
    marker.write_bytes(b'preserve')
    with pytest.raises(ValueError, match='nova e vazia'):
        run(tmp_path / 'result.json', tmp_path)
    assert list(tmp_path.iterdir()) == [marker]
    assert marker.read_bytes() == b'preserve'
