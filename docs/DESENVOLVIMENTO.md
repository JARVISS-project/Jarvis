# Executar, testar e compilar

Código Python em `src/jarvis`; testes em `tests`. Ferramentas internas de
gestão do desenvolvimento e seus registros não fazem parte desta edição.

## Ambiente de desenvolvimento Windows

O perfil fixado usa CPython 3.13.14 x64. Consulte os locks com hashes em
`requirements/windows-py313`; eles são específicos da plataforma.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements/windows-py313/build.lock
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements/windows-py313/package.lock
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements/windows-py313/tests.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pytest -q
```

Para iniciar por texto: `python -m jarvis --ui`, com o ambiente ativado.
Dados de desenvolvimento ficam locais e não devem ser versionados.

O lock `package.lock` cobre o runtime distribuído. `mvp.lock` e o extra
`fast-voice` também abrangem componentes opcionais, incluindo Torch/Transformers,
que ainda precisam de revisão de segurança e não são necessários para usar o `.exe`.

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

## Identidade Google opcional — somente teste

Para preparar um binário com o login JARVIS em teste, passe explicitamente
`--google-client-config CAMINHO_DO_JSON_DESKTOP` ao comando de build completo.
Use apenas o cliente do projeto público separado. O build rejeita outro projeto,
cliente Web, tokens e campos extras. O arquivo fica fora do Git; a opção não é
herdada do ambiente nem descoberta no perfil. Sem a opção, o pacote pede um
cliente próprio, mesmo se um build anterior tivesse identidade incluída.

O JSON registra a aplicação Desktop pública, cujo segredo não pode ser mantido
confidencial em dispositivos distribuídos. Não é credencial de uma conta nem
substitui consentimento; não inclua access_token, refresh_token ou dados do usuário.
Os tokens individuais permanecem nas lojas DPAPI existentes, fora do build.
Não publique a prévia como Google liberado: cadastro/verificação e teste real
de consentimento continuam necessários. Consulte o fluxo oficial Desktop do Google.
