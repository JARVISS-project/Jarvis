"""Prepare a NEW optional Windows source environment; never modify an existing one."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'requirements/windows-py313'
MODEL_FILES = ('ve.pt','t3_mtl23ls_v2.safetensors','s3gen.pt',
               'grapheme_mtl_merged_expanded_v1.json','conds.pt','Cangjie5_TC.json')


def validate_destination(destination):
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise FileExistsError('Escolha uma pasta nova. O ambiente existente será preservado.')
    if destination.is_relative_to(ROOT) and not destination.is_relative_to(ROOT / 'data'):
        raise ValueError('Dentro do checkout, use somente uma subpasta de data para não versionar o ambiente.')
    return destination


def validate_models(directory):
    if directory is None:
        return None
    if str(directory).startswith(('\\\\','//')):
        raise ValueError('Use uma pasta local de modelos.')
    directory = Path(directory).expanduser().resolve()
    if not all((directory / name).is_file() for name in MODEL_FILES):
        raise ValueError('A pasta precisa conter o snapshot multilingual completo. Nenhum modelo foi baixado.')
    return directory


def prepare(destination, *, cuda=False, model_directory=None, profile_root=None,
            run=subprocess.run, create=venv.create):
    if sys.platform != 'win32' or sys.version_info[:3] != (3,13,14) or struct.calcsize('P') != 8:
        raise RuntimeError('Use CPython 3.13.14 x64 no Windows, conforme o perfil validado.')
    destination = validate_destination(destination)
    model_directory = validate_models(model_directory)
    profile_root = Path(profile_root).expanduser().resolve() if profile_root else Path(os.environ['LOCALAPPDATA']) / 'JARVIS/NeuralDesktop'
    if profile_root == destination or profile_root.is_relative_to(destination):
        raise ValueError('O perfil pessoal deve ficar fora da pasta do ambiente.')
    create(destination, with_pip=True)
    python = destination / 'Scripts/python.exe'
    wheels = destination / 'wheels'
    environment = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    environment.pop('PYTHONPATH', None)
    environment.pop('PYTHONHOME', None)
    def execute(arguments):
        run([str(arg) for arg in arguments], check=True, cwd=ROOT, env=environment)
    execute([python, ROOT/'scripts/prepare_neural_wheels.py', '--output', wheels, '--download'])
    lock = PROFILE / ('neural-cuda.lock' if cuda else 'neural.lock')
    execute([python, '-m', 'pip', 'install', '--require-hashes', '--only-binary=:all:',
             '--index-url', 'https://pypi.org/simple', '--find-links', wheels,
             '-r', PROFILE/'build.lock', '-r', lock])
    # All runtime dependencies were resolved and installed with hashes above.
    execute([python, '-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '-e', ROOT])
    execute([python, '-m', 'pip', 'check'])
    launch = {'source_root':str(ROOT), 'profile_root':str(profile_root),
              'model_directory':str(model_directory) if model_directory else None,
              'device':'cuda' if cuda else 'cpu'}
    (destination/'launch-config.json').write_text(json.dumps(launch,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    shutil.copyfile(ROOT/'scripts/neural_desktop.pyw', destination/'launch_jarvis.pyw')
    shutil.copyfile(ROOT/'LICENSE', destination/'JARVIS-LICENSE.txt')
    (destination/'Iniciar-JARVIS.cmd').write_text('@echo off\r\nstart "" "%~dp0Scripts\\pythonw.exe" "%~dp0launch_jarvis.pyw"\r\n',encoding='utf-8',newline='')
    return destination


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',required=True,type=Path)
    parser.add_argument('--cuda',action='store_true',help='Instalar o wheel CUDA 13.2. Não instala drivers e não muda para CPU automaticamente.')
    parser.add_argument('--model-directory',type=Path,help='Snapshot local já adquirido; nenhum modelo TTS é baixado por este preparador.')
    parser.add_argument('--profile-root',type=Path,help='Perfil separado; padrão LOCALAPPDATA/JARVIS/NeuralDesktop.')
    args=parser.parse_args()
    path=prepare(args.destination,cuda=args.cuda,model_directory=args.model_directory,profile_root=args.profile_root)
    print('Ambiente preparado. Abra Iniciar-JARVIS.cmd em '+str(path))
    print('Amostras de voz e contas continuam sendo configuradas pelo próprio usuário no aplicativo.')


if __name__ == '__main__':
    main()
