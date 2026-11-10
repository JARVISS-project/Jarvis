"""User-selected local reference audio; never part of the application bundle."""
from pathlib import Path
import hashlib
import os
import tempfile
import wave


MODEL_FILES = ('ve.pt', 't3_mtl23ls_v2.safetensors', 's3gen.pt',
               'grapheme_mtl_merged_expanded_v1.json', 'conds.pt', 'Cangjie5_TC.json')


def local_model_directory():
    """Only inspect the local cache: no requests, downloads or Torch imports."""
    selected = os.environ.get('JARVIS_NEURAL_MODEL_DIRECTORY')
    if selected is not None:
        # An explicit selection never falls back to a different cached model.
        if len(selected) > 4096 or selected.startswith(('\\\\', '//')) or any(ord(c) < 32 for c in selected):
            return None
        try:
            path = Path(selected).expanduser()
            return path if path.is_absolute() and all((path / name).is_file() for name in MODEL_FILES) else None
        except (OSError, ValueError):
            return None
    try:
        from huggingface_hub import try_to_load_from_cache
        files = [try_to_load_from_cache('ResembleAI/chatterbox', name) for name in MODEL_FILES]
        if all(isinstance(file, str) and Path(file).is_file() for file in files):
            parents = {Path(file).parent for file in files}
            if len(parents) == 1:
                return parents.pop()
    except (ImportError, OSError, ValueError):
        pass
    return None


def load_local_model(device):
    directory = local_model_directory()
    if directory is None:
        raise RuntimeError('Modelo neural não instalado; nenhum download iniciado.')
    from jarvis.tts.neural_compat import is_compatibility_variant, load_cuda_model
    if str(device).startswith('cuda') and is_compatibility_variant():
        return load_cuda_model(directory, device)
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    return ChatterboxMultilingualTTS.from_local(directory, device)


def import_reference(source, directory):
    """Decode a bounded MP3/WAV into a private, stable mono PCM reference."""
    if not isinstance(source, str) or not source or len(source) > 4096:
        raise ValueError('Selecione um arquivo MP3 ou WAV local.')
    path = Path(source)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')) or path.suffix.casefold() not in {'.mp3', '.wav'}:
        raise ValueError('Selecione um arquivo MP3 ou WAV local.')
    if not path.is_file() or not 0 < path.stat().st_size <= 10 * 1024 * 1024:
        raise ValueError('A amostra deve ter até 10 MB.')
    import av
    import numpy as np
    frames, samples = [], 0
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise ValueError('Arquivo sem áudio.')
            resampler = av.AudioResampler(format='s16', layout='mono', rate=24000)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    samples += converted.samples
                    if samples > 30 * 24000:
                        raise ValueError('Use uma amostra entre 3 e 30 segundos.')
                    frames.append(converted.to_ndarray().tobytes())
            for converted in resampler.resample(None):
                samples += converted.samples
                frames.append(converted.to_ndarray().tobytes())
    except ValueError:
        raise
    except Exception:
        raise ValueError('Não foi possível ler o áudio selecionado.') from None
    if not 3 * 24000 <= samples <= 30 * 24000:
        raise ValueError('Use uma amostra entre 3 e 30 segundos.')
    pcm = b''.join(frames)
    if np.abs(np.frombuffer(pcm, dtype='<i2').astype(np.float32)).mean() < 10:
        raise ValueError('A amostra está silenciosa; escolha uma fala audível.')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (hashlib.sha256(pcm).hexdigest() + '.wav')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, suffix='.wav', delete=False) as file:
            temporary = Path(file.name)
        with wave.open(str(temporary), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(pcm)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
