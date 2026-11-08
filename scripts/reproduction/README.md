# Kit pequeno de reprodução — 23AZ

Este kit não certifica Windows novo. Nesta sprint: clone separado no commit
23AY e venv nova no host, locks/hashes, diagnóstico sem modelos e inferência
controlada com artefatos/driver/Ollama existentes. [Relatório](../../docs/sprint-23az-independent-reproduction.md).

## Instalação futura em Windows novo

Obter este kit do commit de entrega 23AZ confirmado (identificar SHA pelo
git log do relatório). O código instalado pelo script é o SHA fixo **23AY**
9a8f82f8b8701dfa4facc4dc2b3ffbf1eb2dfe5e; kit e produto têm identidades distintas.
Requisitos: Git, Windows x64, Python3.13.14 x64 explícito e rede PyPI/PyTorch.

```powershell
$pythonRepro = Read-Host 'Caminho absoluto do Python 3.13.14 x64'
$destinoRepro = Read-Host 'Diretório novo exclusivo, ainda inexistente'
powershell -NoProfile -File scripts/reproduction/install.ps1 -Python $pythonRepro -Destination $destinoRepro
```

[install.ps1](install.ps1) recusa destino existente, valida Python/commit,
cria checkout/venv/cache/tmp próprios, instala locks com hashes e editable,
desliga user-site e restaura variáveis de processo. Não ativa features, instala
driver, desativa proteção, reinicia máquina, baixa modelos ou inicia runtime.
Não flexibilizar ExecutionPolicy se bloqueado: documentar e pedir revisão.
Na 23AZ os passos clone/install foram executados individualmente; wrapper
PowerShell validado por parser, **não executado inteiro ponta a ponta**.

[checks.py](checks.py) usa subprocessos com -I, pip check, CLI help,
16 imports e doctor com settings mínimo versionado; não usa settings habitual.
Diagnóstico consulta apenas serviço local/sistema. Falha/timeout não vira sucesso,
não há retries. Limite de30s fixo; primeira importação CTranslate2 na23AZ excedeu,
preservada na evidência. Checks não carregam modelos/áudio/TTS.
Execute com Python da nova venv, não jarvis.bat que escolhe .venv habitual:

```powershell
& "$destinoRepro/venv/Scripts/python.exe" -I -X utf8 scripts/reproduction/checks.py --checkout "$destinoRepro/checkout"
```

## Identidade — antes de inferência

[Manifesto](../../docs/reproduction-models-23az.json), [verificador](verify_models.py).
Caminhos de artefatos são escolha explícita do operador; nunca busca/download.
Usar uma cópia previamente adquirida/aprovada, não credenciais/pastas pessoais.
STT requer cinco arquivos da revisão; wake três ONNX no ambiente instalado.
Ollama usa API local, tag/digest; não prova identidade byte-a-byte de todo GGUF.
Perfil mínimo medium não é coberto pelo manifesto large-v3: diagnóstico retorna
mismatch/exit1 corretamente. Não trocar o perfil automaticamente para esconder.
Mesmo nome não fixa revisão; conferir cache/arquivos efetivos separadamente.
Verificador --settings compara apenas identificadores de perfil normal;
override de caminho wake e modo game exigem verificação explícita adicional.

```powershell
$modeloSTT = Read-Host 'Diretório local do snapshot STT aprovado'
$wakeLocal = Read-Host 'Diretório dos três ONNX no ambiente isolado'
& "$destinoRepro/venv/Scripts/python.exe" -I scripts/reproduction/verify_models.py --manifest docs/reproduction-models-23az.json --stt-dir $modeloSTT --wake-dir $wakeLocal --ollama
```

## Inferência controlada e checklist VM

Somente quando esta etapa estiver autorizada e arquivos congelados disponíveis:

```powershell
$wavPublico = Read-Host 'WAV público com hash do manifesto'
& "$destinoRepro/venv/Scripts/python.exe" -I -X utf8 scripts/reproduction/infer_stt.py --manifest docs/reproduction-models-23az.json --model $modeloSTT --wav $wavPublico
& "$destinoRepro/venv/Scripts/python.exe" -I -X utf8 scripts/reproduction/infer_wake.py --wav $wavPublico --manifest docs/reproduction-models-23az.json
& "$destinoRepro/venv/Scripts/python.exe" -I -X utf8 scripts/reproduction/infer_ollama.py --manifest docs/reproduction-models-23az.json
```

STT verifica hashes e gate real5781MiB, usa subprocesso/timeout produção,
PATH próprio torch/lib + System32 e métricas DLL/RAM/VRAM/cleanup.
Nenhum kill externo/reclaim/gate reduzido. DLLs carregadas fora da venv causam
diagnóstico de dependência externa; driver continua fornecido pelo SO.
Wake clip público negativo não prova recall; nenhum microfone/Tool/TTS.
Ollama prompt fixo neutro, sem Tools; servidor deve existir, não é iniciado.
Serviço/modelo host reutilizado não comprova provisão independente.

Checklist futuro:
- [ ] Windows/build/Python/CPU/RAM/disco registrados, sem pastas pessoais.
- [ ] VM realmente acessível; GPU passthrough medido, não presumido.
- [ ] Locks e imports completos sem user-site/habitual; primeiro erro preservado.
- [ ] Visual C++/driver/CUDA/cuDNN originados/versionados sem instalar automaticamente.
- [ ] Modelos adquiridos fora deste kit, licenças/revisões/hashes conferidos.
- [ ] Diagnóstico detecta divergência; nenhuma substituição automática.
- [ ] Inferência CPU e GPU rotuladas separadamente; VRAM insuficiente bloqueia.
- [ ] RAM/VRAM/DLLs/resultados/cleanup reais; zero worker residual.
- [ ] Sem áudio audível/efeitos/Tools/segredos; limites host declarados.
- [ ] Só declarar reprodução integral após todos os gates independentes.

Fontes: [faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/master/README.md),
[CTranslate2](https://opennmt.net/CTranslate2/installation.html),
[NVIDIA](https://docs.nvidia.com/deeplearning/cudnn/backend/latest/reference/support-matrix.html),
[PyWinRT](https://pywinrt.readthedocs.io/en/latest/).
