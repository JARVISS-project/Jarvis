# Componentes de terceiros

O código original do JARVIS é disponibilizado sob **GPL-3.0-or-later**.
Essa licença não substitui as licenças dos componentes de terceiros.
Os avisos completos e o inventário de versões estão em `_internal/licenses`
no pacote Windows. Os modelos de IA não acompanham o pacote.

## Fontes correspondentes

Cada release binária deve disponibilizar, na mesma página de download:

- código do JARVIS e seus scripts de build, na tag da release;
- `JARVIS-Fontes-Terceiros-0.1.3-public2.zip`, com os arquivos de fonte,
  receitas/patches do fornecedor e manifesto SHA-256;
- `SHA256SUMS.txt`, com os hashes dos downloads publicados.

O lock [sources.lock.json](packaging/windows/sources.lock.json) fixa URLs,
versões/commits e hashes dos 35 arquivos de fonte. Para reobter esses mesmos
arquivos, use `scripts/prepare_windows_sources.py --plan
packaging/windows/sources.lock.json --output data/third-party-sources`.
Esse script apenas baixa e verifica arquivos: não executa código de terceiros.

Os fontes abrangem Piper 1.7.0 e o commit espeak-ng usado por seu CMake,
PySide/Shiboken 6.11.2 e os módulos Qt usados, PyAV 16.1.0, FFmpeg 8.0.1,
as receitas/patches pyav-ffmpeg 8.0.1-3 e suas dependências. Quando o servidor
de um tarball estava indisponível, o lock usa o commit da mesma versão no
repositório oficial e preserva o hash do tarball original como referência.
Para snapshots Git, siga as instruções upstream de bootstrap/autogen antes do build.

## Condições preservadas

- Piper é GPL-3.0-or-later; seus avisos e fontes completos acompanham a release.
- Qt/PySide usam as licenças abertas aplicáveis aos módulos. A ligação é dinâmica;
  substituição/relink e engenharia reversa para depuração de modificações são permitidos.
  Nenhuma assinatura ou bloqueio próprio do JARVIS impede bibliotecas modificadas.
- FFmpeg e codecs conservam suas licenças. O wheel inclui bibliotecas x264/x265;
  o texto LGPL retornado por uma API não substitui a análise dos componentes.
  Os respectivos fontes e os patches do fornecedor foram preservados no kit.
- Demais dependências conservam seus avisos BSD/MIT/Apache e demais termos,
  conforme inventário e arquivos de licença do pacote.
- CUDA/driver, Ollama e modelos são pré-requisitos separados e não foram copiados
  do computador do autor para a distribuição. Cada um conserva seus próprios termos.

As instruções de reprodução estão em [Windows](docs/manual/WINDOWS_EXECUTAVEL.md)
e [dependências fixadas](requirements/windows-py313/README.md).
