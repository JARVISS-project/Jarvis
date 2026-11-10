"""Low-memory CUDA loading for the explicitly prepared compatibility variant."""
from importlib.metadata import PackageNotFoundError, version


def is_compatibility_variant():
    try:
        return version('chatterbox-tts') == '0.1.7+jarvis.4'
    except PackageNotFoundError:
        return False


def copy_safetensors(model, path, *, open_weights=None):
    """Validate the complete shape map, then close each mapped CPU tensor promptly.

    Keeping all mapped tensors alive during a CUDA transfer can consume several
    additional GB of physical RAM. The destination parameters already exist on
    the GPU; copying one tensor at a time preserves their identity and dtype.
    """
    import torch
    if open_weights is None:
        from safetensors import safe_open
        open_weights = safe_open
    target = model.state_dict()
    with open_weights(path, framework='pt', device='cpu') as weights:
        keys = list(weights.keys())
        if set(keys) != set(target) or any(
                tuple(weights.get_slice(key).get_shape()) != tuple(target[key].shape) for key in keys):
            raise ValueError('O checkpoint não corresponde ao modelo neural esperado.')
    with torch.no_grad():
        for key in keys:
            with open_weights(path, framework='pt', device='cpu') as weights:
                tensor = weights.get_tensor(key)
                try:
                    if tensor.dtype != target[key].dtype:
                        raise ValueError('O tipo do checkpoint neural é incompatível.')
                    target[key].copy_(tensor)
                finally:
                    del tensor


def load_cuda_model(directory, device):
    import torch
    from chatterbox.mtl_tts import (
        ChatterboxMultilingualTTS, Conditionals, MTLTokenizer, S3Gen,
        T3, T3Config, VoiceEncoder,
    )
    with torch.device(device):
        encoder = VoiceEncoder()
    encoder.to(device)
    encoder.load_state_dict(torch.load(directory / 've.pt', map_location=device, weights_only=True))
    encoder.eval()
    with torch.device(device):
        t3 = T3(T3Config.multilingual())
    t3.to(device)
    copy_safetensors(t3, directory / 't3_mtl23ls_v2.safetensors')
    t3.eval()
    with torch.device(device):
        generator = S3Gen()
    generator.to(device)
    generator.load_state_dict(torch.load(directory / 's3gen.pt', map_location=device, weights_only=True))
    generator.eval()
    tokenizer = MTLTokenizer(str(directory / 'grapheme_mtl_merged_expanded_v1.json'))
    conditionals = Conditionals.load(directory / 'conds.pt', map_location=device).to(device)
    return ChatterboxMultilingualTTS(t3, generator, encoder, tokenizer, device, conds=conditionals)
