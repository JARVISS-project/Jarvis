# Executar, testar e compilar

Código Python em `src/jarvis`; testes em `tests`. Ferramentas internas de
gestão do desenvolvimento e seus registros não fazem parte desta edição.

## Ambiente de desenvolvimento Windows

O perfil fixado usa CPython 3.13.14 x64. Consulte os locks com hashes em
`requirements/windows-py313`; eles são específicos da plataforma.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements/windows-py313/build.lock
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements/windows-py313/mvp.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pytest -q
```

Para iniciar por texto: `python -m jarvis --ui`, com o ambiente ativado.
Dados de desenvolvimento ficam locais e não devem ser versionados.

## Pacote Windows

Build usa ambiente isolado, PyInstaller e Inno Setup nas versões de
`packaging/windows/toolchain.json`. Os nomes históricos de scripts permanecem
estáveis para compatibilidade; a identidade atual está em `runtime/release.py`.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python scripts/build_windows_23bo.py --prepare
python scripts/build_windows_23bo.py --output-dir dist/public
python scripts/build_windows_23bo.py --installer --output-dir dist/public
python scripts/prepare_windows_sources.py --plan packaging/windows/sources.lock.json --output data/third-party-sources
```

Publique binários junto com fontes correspondentes, avisos e hashes.
Nunca use o perfil pessoal como entrada do build. O teste de instalação usa
identidade e perfil temporários; testes não devem gravar áudio nem acessar contas.
