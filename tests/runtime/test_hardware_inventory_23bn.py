import io
import json
import urllib.error

import pytest

from jarvis.config import JarvisConfig
from jarvis.runtime import hardware
from jarvis.speech.gpu_budget import GpuBudgetManager, GpuMemory


def report(tmp_path, *, ram=None, models=None, gpu=None):
    cfg = JarvisConfig(settings_path=tmp_path / 'settings.json')
    def probe():
        if isinstance(gpu, Exception):
            raise gpu
        return gpu if gpu is not None else GpuMemory(12288, 2048, 10240)
    return hardware.hardware_report(cfg,
        memory_probe=lambda: ram if ram is not None else {'status': 'ready', 'total_bytes': 16 * 1024**3, 'available_bytes': 8 * 1024**3},
        model_probe=lambda: models if models is not None else {'status': 'ready', 'models': [{'name': cfg.model_name, 'size_bytes': 2497293803}]},
        gpu_probe=lambda: {'status': 'ready', 'devices': ['fake GPU']}, gpu_budget=GpuBudgetManager(probe))


@pytest.mark.parametrize('gpu,status', [(RuntimeError('absent'), 'unavailable'), (GpuMemory(4096, 3000, 1096), 'insufficient'), (GpuMemory(-1, 0, 0), 'invalid'), ('invalid', 'invalid')])
def test_missing_low_or_invalid_gpu_preserves_gate_and_profile(tmp_path, gpu, status):
    data = report(tmp_path, gpu=gpu)
    assert data['vram']['status'] == status
    assert data['vram']['required_mib'] == 5781
    assert data['profiles'][1]['status'] == 'não recomendado agora'
    assert data['current_profile'] == 'medium'
    assert not (tmp_path / 'settings.json').exists()


@pytest.mark.parametrize('ram', [{}, {'status': 'ready', 'total_bytes': 1, 'available_bytes': 2}, {'status': 'ready', 'total_bytes': 'invalid', 'available_bytes': 1}])
def test_invalid_ram_unavailable(tmp_path, ram):
    assert report(tmp_path, ram=ram)['ram']['status'] == 'unavailable'


def test_low_ram_missing_model_down_and_recovery(tmp_path):
    data = report(tmp_path, ram={'status': 'ready', 'total_bytes': 8 * 1024**3, 'available_bytes': 1024**3}, models={'status': 'unavailable', 'models': []})
    assert any('Pouca RAM' in w for w in data['warnings'])
    assert data['assistant']['status'] == 'unavailable'
    assert report(tmp_path, models={'status': 'ready', 'models': []})['assistant']['status'] == 'unavailable'
    recovered = report(tmp_path)
    assert recovered['assistant']['status'] == 'ready'
    assert recovered['ram']['total_bytes'] > recovered['ram']['available_bytes']
    assert not recovered['backends']['external_supported']


class Response(io.BytesIO):
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()


@pytest.mark.parametrize('body,status', [(b'{bad', 'unavailable'), (b'{"models":{}}', 'unavailable'), (b'{"models":[]}', 'ready'), (b'{"models":[{"name":"a","size":1}]}', 'ready'), (b'{"models":[{"name":"a","size":-1}]}', 'unavailable'), (b'x' * (1024*1024 + 1), 'unavailable')], ids=['json', 'schema', 'empty', 'model', 'negative', 'size'])
def test_local_models_bounds_fixed_loopback_and_timeout(monkeypatch, body, status):
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == 'http://127.0.0.1:11434/api/tags'
            assert request.method == 'GET' and timeout == 2
            return Response(body)
    def build(*handlers):
        assert isinstance(handlers[0], hardware.urllib.request.ProxyHandler) and handlers[0].proxies == {}
        assert isinstance(handlers[1], hardware._NoRedirect)
        return Opener()
    monkeypatch.setattr(hardware.urllib.request, 'build_opener', build)
    assert hardware.local_models()['status'] == status


def test_no_redirect_and_timeout_recovery(monkeypatch):
    with pytest.raises(urllib.error.URLError):
        hardware._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://external.example')
    class Opener:
        def open(self, *args, **kwargs):
            raise TimeoutError
    monkeypatch.setattr(hardware.urllib.request, 'build_opener', lambda *a: Opener())
    assert hardware.local_models()['status'] == 'unavailable'
