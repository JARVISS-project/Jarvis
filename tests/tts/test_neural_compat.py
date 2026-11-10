from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import sys
import weakref

import pytest

from jarvis.tts.neural_compat import copy_safetensors, is_compatibility_variant
from jarvis.tts.reference import MODEL_FILES, local_model_directory, load_local_model
from tests.application.test_conversation_preferences_23bm import config_with_voice
from jarvis.application.conversation_preferences import ConversationPreferences


def test_explicit_model_directory_is_local_and_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv('JARVIS_NEURAL_MODEL_DIRECTORY', str(tmp_path))
    assert local_model_directory() is None
    for name in MODEL_FILES:
        (tmp_path / name).write_bytes(b'synthetic; never loaded')
    assert local_model_directory() == tmp_path
    monkeypatch.setenv('JARVIS_NEURAL_MODEL_DIRECTORY', '//server/models')
    assert local_model_directory() is None
    monkeypatch.setenv('JARVIS_NEURAL_MODEL_DIRECTORY', 'relative/models')
    assert local_model_directory() is None
    monkeypatch.setenv('JARVIS_NEURAL_MODEL_DIRECTORY', '')
    assert local_model_directory() is None


@pytest.mark.parametrize('installed,expected', [('0.1.7',False),('0.1.7+jarvis.4',True),('0.2.0',False)])
def test_optimized_loader_is_only_for_the_validated_variant(monkeypatch, installed, expected):
    monkeypatch.setattr('jarvis.tts.neural_compat.version', lambda _name: installed)
    assert is_compatibility_variant() is expected


def test_dispatch_preserves_original_provider_and_cpu_loader(tmp_path, monkeypatch):
    monkeypatch.setattr('jarvis.tts.reference.local_model_directory', lambda: tmp_path)
    monkeypatch.setitem(sys.modules, 'chatterbox', SimpleNamespace())
    calls = []
    loader = SimpleNamespace(from_local=lambda folder, device: calls.append(('upstream',device)))
    monkeypatch.setitem(sys.modules, 'chatterbox.mtl_tts', SimpleNamespace(ChatterboxMultilingualTTS=loader))
    monkeypatch.setattr('jarvis.tts.neural_compat.load_cuda_model', lambda folder, device: calls.append(('compat',device)))
    monkeypatch.setattr('jarvis.tts.neural_compat.version', lambda _name: '0.1.7')
    load_local_model('cuda')
    monkeypatch.setattr('jarvis.tts.neural_compat.version', lambda _name: '0.1.7+jarvis.4')
    load_local_model('cpu')
    load_local_model('cuda')
    assert calls == [('upstream','cuda'),('upstream','cpu'),('compat','cuda')]


def mapped_model(monkeypatch, *, wrong_shape=False, missing=False, wrong_dtype=False):
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(no_grad=nullcontext))
    copies, opened, references = [], [], []
    class Tensor:
        shape = (2,)
        dtype = 'float32'
        def __init__(self,value): self.value=value
    class Target(Tensor):
        def copy_(self,source):
            assert opened[-1].active
            copies.append(source.value)
    targets = {'a':Target(None),'b':Target(None)}
    class Weights:
        active = False
        def __enter__(self): self.active=True;opened.append(self);return self
        def __exit__(self,*args):
            self.active=False
            assert all(reference() is None for reference in references), 'Mapped CPU tensor retained after closing file.'
        def keys(self): return ['a'] if missing else ['a','b']
        def get_slice(self,key): return SimpleNamespace(get_shape=lambda:(3,) if wrong_shape else (2,))
        def get_tensor(self,key):
            tensor=Tensor(key)
            if wrong_dtype: tensor.dtype='float16'
            references.append(weakref.ref(tensor))
            return tensor
    return SimpleNamespace(state_dict=lambda:targets), lambda *args,**kwargs:Weights(), copies, opened


def test_streaming_copy_closes_each_mapping_without_retaining_cpu_tensors(monkeypatch):
    model, opener, copies, opened=mapped_model(monkeypatch)
    copy_safetensors(model, Path('synthetic.safetensors'), open_weights=opener)
    assert copies==['a','b'] and len(opened)==3
    assert all(not weight.active for weight in opened)


@pytest.mark.parametrize('change', ['wrong_shape','missing'])
def test_shape_and_key_mismatch_refused_before_any_copy(monkeypatch, change):
    model, opener, copies, _=mapped_model(monkeypatch, **{change:True})
    with pytest.raises(ValueError):
        copy_safetensors(model, Path('synthetic.safetensors'), open_weights=opener)
    assert not copies


def test_dtype_refusal_also_releases_the_cpu_mapping(monkeypatch):
    model, opener, copies, opened=mapped_model(monkeypatch, wrong_dtype=True)
    with pytest.raises(ValueError):
        copy_safetensors(model, Path('synthetic.safetensors'), open_weights=opener)
    assert not copies and all(not weight.active for weight in opened)


def test_unavailable_neural_voice_cannot_download_or_fall_back(tmp_path, monkeypatch):
    config=config_with_voice(tmp_path,monkeypatch)
    config.tts_provider='chatterbox'
    config.tts_enabled=True
    calls=[]
    service=ConversationPreferences(config,factory=lambda cfg:calls.append(cfg))
    monkeypatch.setattr('jarvis.application.conversation_preferences.local_model_directory', lambda:None)
    try:
        with pytest.raises(RuntimeError,match='Nenhuma troca automática'):
            service.speak_async('Olá')
        assert not calls and service._backend is None
    finally:
        service.close()
