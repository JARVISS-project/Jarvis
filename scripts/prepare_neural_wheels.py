"""Build explicit JARVIS compatibility variants from hash-pinned upstream wheels.

No vendor code is executed. No package is installed and no voice model is fetched.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / 'packaging/neural/variants.lock.json'
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_CONTENT = 120 * 1024 * 1024
ALIGNMENT_OLD = 'alignment_repetition = self.complete and (A[self.completed_at:, :-5].max(dim=1).values.sum() > 5)'
ALIGNMENT_NEW = 'alignment_repetition = self.complete and A.size(1) > 5 and (A[self.completed_at:, :-5].max(dim=1).values.sum() > 5)'
LOADER_PATCHES = {
    'map_location = None': 'map_location = torch.device(device)',
    've = VoiceEncoder()': 'with torch.device(device):\n            ve = VoiceEncoder()\n        ve.to(device)',
    't3 = T3(T3Config.multilingual())': 'with torch.device(device):\n            t3 = T3(T3Config.multilingual())\n        t3.to(device)',
    'load_safetensors(ckpt_dir / "t3_mtl23ls_v2.safetensors")': 'load_safetensors(ckpt_dir / "t3_mtl23ls_v2.safetensors", device=str(map_location))',
    't3.load_state_dict(t3_state)': 't3.load_state_dict(t3_state)\n        del t3_state',
    's3gen = S3Gen()': 'with torch.device(device):\n            s3gen = S3Gen()\n        s3gen.to(device)',
}


def sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError('Upstream source differs from the reviewed patch.')
    return text.replace(old, new)


def read_wheel(source: Path, expected: str) -> dict[str, bytes]:
    if not source.is_file() or source.stat().st_size > MAX_ARCHIVE or sha256(source) != expected:
        raise ValueError('Upstream wheel SHA-256 mismatch.')
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > 300 or sum(info.file_size for info in infos) > MAX_CONTENT:
            raise ValueError('Upstream archive exceeds the reviewed limits.')
        names = set()
        for info in infos:
            name = info.filename
            original = info.orig_filename
            if (not name or name in names or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                    or '\\' in original or ':' in original or '\x00' in original
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError('Unsafe upstream archive member.')
            names.add(name)
        return {info.filename: archive.read(info) for info in infos if not info.is_dir()}


def variant_content(source: Path, recipe: dict) -> dict[str, bytes]:
    files = read_wheel(source, recipe['sha256'])
    dist = recipe['name'].replace('-', '_') + '-' + recipe['version'] + '.dist-info'
    new_dist = recipe['name'].replace('-', '_') + '-' + recipe['variant'] + '.dist-info'
    if ([name for name in files if name.endswith('.dist-info/METADATA')] != [dist + '/METADATA']
            or any(name.endswith(('/RECORD.jws', '/RECORD.p7s')) for name in files)):
        raise ValueError('Unexpected or signed wheel metadata.')
    metadata = files[dist + '/METADATA'].decode('utf-8')
    if 'Name: ' + recipe['name'] + '\n' not in metadata:
        raise ValueError('Unexpected upstream distribution name.')
    files = {(new_dist + name[len(dist):] if name.startswith(dist + '/') else name): data
             for name, data in files.items()}
    header, separator, body = metadata.partition('\n\n')
    if not separator:
        raise ValueError('Malformed upstream metadata.')
    header = replace_once(header, 'Version: ' + recipe['version'] + '\n', 'Version: ' + recipe['variant'] + '\n')
    if recipe['name'] == 'chatterbox-tts':
        remove = ('torch', 'torchaudio', 'transformers', 'diffusers', 'safetensors', 'resemble-perth', 'gradio')
        def retained(line):
            return not any(line.startswith('Requires-Dist: ' + name)
                           and line[len('Requires-Dist: ' + name):][:1] in ('', '<', '>', '=', ';', '!', '~', ' ')
                           for name in remove)
        header = '\n'.join(line for line in header.splitlines() if retained(line))
        header += '\n' + '\n'.join('Requires-Dist: ' + value for value in (
            'torch>=2.15,<3', 'torchaudio==2.11.1', 'transformers>=5.22.2,<6',
            'diffusers==0.42.1', 'safetensors>=0.8,<1', 'resemble-perth==1.0.1+jarvis.2'))
        path = 'chatterbox/models/t3/inference/alignment_stream_analyzer.py'
        files[path] = replace_once(files[path].decode('utf-8'), ALIGNMENT_OLD, ALIGNMENT_NEW).encode('utf-8')
        path = 'chatterbox/mtl_tts.py'
        text = files[path].decode('utf-8')
        for old, new in LOADER_PATCHES.items():
            text = replace_once(text, old, new)
        files[path] = text.encode('utf-8')
        files[new_dist + '/licenses/Apache-2.0.txt'] = (ROOT / 'packaging/neural/LICENSE-APACHE-2.0.txt').read_bytes()
        header += '\nLicense-File: Apache-2.0.txt'
        changes = ('Corrected Torch/Transformers/Diffusers dependency floors; demo-only Gradio omitted.\n'
                   'Short-text empty alignment reduction guarded; normal alignment checks retained.\n'
                   'Multilingual models constructed and weights loaded on the selected device to reduce RAM peaks.\n')
    elif recipe['name'] == 'resemble-perth':
        path = 'perth/perth_net/__init__.py'
        text = replace_once(files[path].decode('utf-8'), 'from pkg_resources import resource_filename',
                            'from importlib.resources import files')
        files[path] = replace_once(text, 'resource_filename(__name__, "pretrained")',
                                   'str(files(__package__) / "pretrained")').encode('utf-8')
        changes = 'Resource lookup migrated to importlib.resources. Watermark implementation and pretrained assets unchanged.\n'
    else:
        raise ValueError('Distribution is not part of the reviewed recipe.')
    files[new_dist + '/METADATA'] = (header + '\n\n' + body).encode('utf-8')
    files[new_dist + '/JARVIS_COMPATIBILITY.txt'] = (
        'JARVIS compatibility variant, not an upstream release.\n'
        'Original upstream wheel: ' + recipe['filename'] + '\n'
        'Upstream SHA-256: ' + recipe['sha256'] + '\n' + changes +
        'Original copyright and licenses retained. No personal voice samples or base TTS models added.\n'
        'Reproduction recipe: scripts/prepare_neural_wheels.py in the JARVIS source repository.\n').encode('utf-8')
    record = new_dist + '/RECORD'
    files.pop(record, None)
    rows = [[name, 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('='), str(len(data))]
            for name, data in sorted(files.items())]
    table = io.StringIO(newline='')
    csv.writer(table, lineterminator='\n').writerows(rows + [[record, '', '']])
    files[record] = table.getvalue().encode('utf-8')
    return files


def build_variant(source: Path, output: Path, recipe: dict) -> Path:
    files = variant_content(source, recipe)
    filename = recipe['filename'].replace(recipe['version'], recipe['variant'], 1)
    if Path(filename).name != filename:
        raise ValueError('Invalid variant filename.')
    output.mkdir(parents=True, exist_ok=True)
    target = output / filename
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output, suffix='.whl', delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        if target.exists():
            if sha256(target) != sha256(temporary):
                raise FileExistsError('Existing output differs; choose a new output directory.')
        else:
            os.replace(temporary, target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def acquire(cache: Path, recipe: dict, *, download: bool) -> Path:
    target = cache / recipe['filename']
    if target.exists():
        if sha256(target) != recipe['sha256']:
            raise ValueError('Cached upstream wheel differs; nothing was overwritten.')
        return target
    if not download:
        raise FileNotFoundError('Upstream wheel missing. Use --download to fetch the pinned files explicitly.')
    url = urlparse(recipe['url'])
    if url.scheme != 'https' or url.hostname != 'files.pythonhosted.org':
        raise ValueError('Unapproved upstream host.')
    cache.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            temporary = Path(stream.name)
            size = 0
            with urlopen(recipe['url'], timeout=45) as response:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARCHIVE:
                        raise ValueError('Upstream download exceeded its limit.')
                    stream.write(chunk)
        if sha256(temporary) != recipe['sha256']:
            raise ValueError('Downloaded upstream wheel SHA-256 mismatch.')
        if target.exists():
            raise FileExistsError('Cache changed while downloading.')
        os.replace(temporary, target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    cache = args.cache.resolve() if args.cache else output / 'upstream'
    results = []
    for recipe in json.loads(RECIPES.read_text(encoding='utf-8')):
        source = acquire(cache, recipe, download=args.download)
        wheel = build_variant(source, output, recipe)
        results.append({'name': recipe['name'], 'version': recipe['variant'], 'filename': wheel.name,
                        'sha256': sha256(wheel), 'bytes': wheel.stat().st_size,
                        'upstream_sha256': recipe['sha256'], 'upstream_url': recipe['url']})
    (output / 'variants-manifest.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    print('Prepared compatibility wheels. No packages installed; no voice models downloaded.')


if __name__ == '__main__':
    main()
