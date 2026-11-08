from pathlib import Path

import pytest

from jarvis.runtime.package_check import check_stt_config
from jarvis.speech.gpu_budget import GpuBudgetManager


def test_explicit_cpu_acceptance_never_reads_or_relaxes_gpu_gate(monkeypatch):
    monkeypatch.setattr(GpuBudgetManager, 'require', lambda *a: pytest.fail('GPU used by CPU test'))
    config = check_stt_config(Path('synthetic-model'), 'cpu')
    assert config.device == 'cpu' and config.compute_type == 'int8'


def test_cuda_acceptance_propagates_budget_failure_instead_of_cpu_fallback(monkeypatch):
    def deny(self, required):
        assert required == 5781
        raise RuntimeError('budget denied')
    monkeypatch.setattr(GpuBudgetManager, 'require', deny)
    with pytest.raises(RuntimeError, match='budget denied'):
        check_stt_config(Path('synthetic-model'), 'cuda')
