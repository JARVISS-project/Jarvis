"""Launcher template copied into an explicitly prepared local environment."""
import json
import os
from pathlib import Path
import sys


def main():
    settings = json.loads(Path(__file__).with_name('launch-config.json').read_text(encoding='utf-8'))
    source = Path(settings['source_root'])
    if not (source / 'src/jarvis/runtime/packaged.py').is_file():
        raise RuntimeError('A pasta do código do JARVIS foi movida ou removida.')
    # A separate profile must not inherit integrations from another installation.
    for key in list(os.environ):
        if key.startswith('JARVIS_'):
            os.environ.pop(key)
    sys.path.insert(0, str(source / 'src'))
    os.environ['PYTHONPATH'] = str(source / 'src')
    from jarvis.runtime.packaged import configure_profile
    profile = configure_profile(Path(settings['profile_root']))
    if settings['model_directory']:
        os.environ['JARVIS_NEURAL_MODEL_DIRECTORY'] = settings['model_directory']
    os.environ['JARVIS_TTS_DEVICE'] = settings['device']
    os.environ['OMP_NUM_THREADS'] = '2'
    log = (profile / 'logs' / f'neural-launcher-{os.getpid()}.log').open('a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log
    if '--check' in sys.argv:
        from jarvis.config import JarvisConfig
        from jarvis.application.conversation_preferences import ConversationPreferences
        service = ConversationPreferences(JarvisConfig())
        try:
            data = service.inventory()
            result = {key:data[key] for key in ('provider','reference','neural_installed','neural_ready','available','diagnostic')}
            (profile / 'neural-launcher-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            service.close()
        return
    from jarvis.runtime.cli import main as runtime_main
    runtime_main(['--runtime-stop'] if '--stop' in sys.argv else ['--ui'])


if __name__ == '__main__':
    main()
