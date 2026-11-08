"""Bounded read-only hardware inventory; never imports inference backends."""
from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import urllib.error
import urllib.request

from jarvis.runtime.diagnostic import check_gpu, check_tts, _installed
from jarvis.speech.gpu_budget import GpuBudgetManager
from jarvis.speech.profile import parse_stt_profile


def memory_snapshot() -> dict:
    try:
        if os.name == 'nt':
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                            *[(name, ctypes.c_ulonglong) for name in ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                raise OSError
            total, available = status.total, status.available
        else:
            total = os.sysconf('SC_PHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
            # Linux MemAvailable includes reclaimable memory; do not confuse it with MemFree.
            with open('/proc/meminfo', encoding='ascii') as stream:
                values = dict(line.split(':', 1) for line in stream)
            available = int(values['MemAvailable'].split()[0]) * 1024
        if not 0 <= available <= total or total <= 0:
            raise ValueError
        return dict(status='ready', total_bytes=total, available_bytes=available)
    except (OSError, ValueError, KeyError, AttributeError):
        return dict(status='unavailable', total_bytes=None, available_bytes=None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError('Local diagnostic refuses redirects.')


def local_models() -> dict:
    """Same GET /api/tags as doctor, with bounded parsing and loopback only."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request('http://127.0.0.1:11434/api/tags', method='GET')
    try:
        with opener.open(request, timeout=2.0) as response:
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024 or response.status != 200:
                raise ValueError
            data = json.loads(body)
        raw = data.get('models') if isinstance(data, dict) else None
        if not isinstance(raw, list) or len(raw) > 256:
            raise ValueError
        models = []
        for row in raw:
            if not isinstance(row, dict) or not isinstance(row.get('name'), str) or not 0 < len(row['name']) <= 200:
                raise ValueError
            if any(ord(c) < 32 for c in row['name']) or type(row.get('size')) is not int or row['size'] < 0:
                raise ValueError
            models.append(dict(name=row['name'], size_bytes=row['size']))
        return dict(status='ready', models=models, backend='Ollama local')
    except (ValueError, UnicodeError, urllib.error.URLError, TimeoutError, OSError):
        return dict(status='unavailable', models=[], backend='Ollama local')


def hardware_report(config, *, memory_probe=memory_snapshot, model_probe=local_models,
                    gpu_probe=check_gpu, gpu_budget=None) -> dict:
    ram = memory_probe()
    if not isinstance(ram, dict) or ram.get('status') != 'ready' or any(type(ram.get(k)) is not int for k in ('total_bytes', 'available_bytes')) or not 0 <= ram['available_bytes'] <= ram['total_bytes'] or ram['total_bytes'] <= 0:
        ram = dict(status='unavailable', total_bytes=None, available_bytes=None)
    gpu = gpu_probe()
    if not isinstance(gpu, dict):
        gpu = dict(status='unavailable')
    # No reclaim callbacks: assess is observation only, never unloads models.
    budget = (gpu_budget or GpuBudgetManager()).assess(parse_stt_profile('large-v3').minimum_free_vram_mib)
    vram = dict(status=budget.status, total_mib=budget.memory.total_mib if budget.memory and budget.status != 'invalid' else None,
                free_mib=budget.memory.free_mib if budget.memory and budget.status != 'invalid' else None,
                required_mib=budget.required_mib)
    models = model_probe()
    if not isinstance(models, dict) or models.get('status') not in {'ready', 'unavailable'} or not isinstance(models.get('models'), list):
        models = dict(status='unavailable', models=[], backend='Ollama local')
    installed = any(m.get('name') == config.model_name for m in models['models'] if isinstance(m, dict))
    assistant = dict(status='ready' if installed and models['status'] == 'ready' else 'unavailable',
                     configured_model=config.model_name,
                     next_action='Use Conversa. Disponível não garante desempenho.' if installed else 'Inicie Ollama e confira se o modelo configurado está instalado; repita o diagnóstico. Nenhum download automático.')
    profiles = [dict(profile='medium', status='opção existente; desempenho não medido',
                     reason='Perfil STT padrão medium, auto/auto. O backend mantém sua política existente de dispositivo; ajuda não aplica fallback.')]
    if budget.status == 'ready':
        profiles.append(dict(profile='large-v3', status='VRAM livre atende ao gate atual; compatibilidade ainda precisa de validação',
                             reason=f'Gate existente exige {budget.required_mib} MiB livres agora. CUDA/float16 sem fallback; sondagem de VRAM não comprova bibliotecas CUDA nem desempenho.'))
    else:
        profiles.append(dict(profile='large-v3', status='não recomendado agora',
                             reason=f'VRAM {budget.status}; gate existente exige {budget.required_mib} MiB livres. Não reduza o gate.'))
    warnings = ['Inferência e benchmarks não executados. Instalação de modelo não comprova velocidade nem qualidade.',
                'GPU: somente NVIDIA via nvidia-smi; ausência da sonda não prova ausência de GPU. CUDA não inicializado. Orçamento usa a primeira GPU, como o gate existente.',
                'Modelos STT/cache não verificados. O backend de voz existente pode baixar modelos ausentes quando ativado; configure o cache antes de uso offline. Ajuda não ativa voz.']
    if ram['status'] != 'ready':
        warnings.append('RAM indisponível: não há base para recomendar modelos maiores.')
    elif ram['available_bytes'] < 4 * 1024**3:
        warnings.append('Pouca RAM disponível agora (menos de 4 GiB): margem conservadora de atenção, não requisito medido. Prefira texto e confira recursos antes de ativar voz.')
    else:
        warnings.append('Mantenha o modelo local já configurado se disponível. Não há medição nesta leitura que justifique trocar para um modelo maior.')
    try:
        disk = shutil.disk_usage(config.settings_path.resolve().parent if config.settings_path.parent.exists() else os.getcwd())
        storage = dict(status='ready', total_bytes=disk.total, free_bytes=disk.free)
    except OSError:
        storage = dict(status='unavailable', total_bytes=None, free_bytes=None)
    return dict(system=dict(os=platform.system(), version=platform.release(), architecture=platform.machine(), cpu=platform.processor() or 'não identificado', logical_cpus=os.cpu_count()),
                ram=ram, disk=storage, gpu=gpu, vram=vram, ollama=models, assistant=assistant,
                backends=dict(tts=check_tts(config), stt_dependencies_installed=_installed('faster_whisper') and _installed('ctranslate2'), external_supported=False),
                profiles=profiles, warnings=warnings, current_profile=config.stt_profile,
                explanation='Leitura local: sem modelo carregado, benchmark, microfone, áudio, OAuth, Tool, download ou envio externo. Aplicar só após prévia e escolha explícita; ativação requer reinício.')
