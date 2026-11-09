# PyInstaller onedir: allowlisted source/resources, no profile/cache/model trees.
import json
import os
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parents[1]
sys.path.insert(0, str(root))
from scripts.windows_build_environment import select_native_binaries
resources = json.loads((root / 'docs/sprint-23ad-capability-catalog.json').read_text(encoding='utf-8'))
datas = [(str(root / 'docs/sprint-23ad-capability-catalog.json'), 'docs'),
         (str(root / 'packaging/windows/jarvis.svg'), 'assets'),
         (str(root / 'docs/manual/WINDOWS_EXECUTAVEL.md'), 'manual'),
         (str(root / 'data/packaging-public/licenses'), 'licenses')]
google_login = os.environ.get('JARVIS_BUILD_GOOGLE_LOGIN')
if google_login:
    from jarvis.integrations.google.public_login import validate_public_client
    validate_public_client(Path(google_login))
    # A shared Desktop client identifies JARVIS. User tokens never enter the build.
    datas.append((google_login, 'jarvis/integrations/google'))
for relative in resources['source_sha256']:
    datas.append((str(root / relative), str(Path(relative).parent)))
hiddenimports = collect_submodules('jarvis', filter=lambda name: not name.startswith('jarvis.orca'))
binaries = []
for package in ['piper', 'faster_whisper', 'ctranslate2', 'pvrecorder',
                'sounddevice', 'winrt', 'pyttsx3']:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
for distribution in ['piper-tts', 'faster-whisper', 'ctranslate2', 'google-auth',
                     'google-auth-oauthlib', 'tzdata', 'onnxruntime', 'pywin32',
                     'winrt-runtime', 'sounddevice']:
    datas += copy_metadata(distribution)
a = Analysis([str(root / 'packaging/windows/entry.py')], pathex=[str(root / 'src')],
             binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=['torch', 'transformers', 'chatterbox', 'playwright', 'pytest',
                       'tkinter', 'matplotlib', 'IPython', 'jarvis.orca_play', 'jarvis.orca_cli', 'jarvis.orca_worker'],
             noarchive=False)
if google_login:
    a.datas = [('jarvis/integrations/google/public-login.json', source, kind)
               if Path(source).resolve() == Path(google_login).resolve() else (name, source, kind)
               for name, source, kind in a.datas]
pyz = PYZ(a.pure)
# MSVC is a separately provisioned prerequisite; no System32 redistribution.
a.binaries = select_native_binaries(a.binaries, venv=root / 'data/packaging-public/venv',
                                   python=Path(sys.base_prefix), windows=Path(os.environ['SystemRoot']))
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='JARVIS',
          console=False, debug=False, strip=False, upx=False,
          icon=str(root / 'data/packaging-public/jarvis.ico'),
          version=str(root / 'packaging/windows/version.txt'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='JARVIS')
