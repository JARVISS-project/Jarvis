# Componentes de terceiros

O cÃ³digo original do JARVIS Ã© disponibilizado sob **GPL-3.0-or-later**.
Essa licenÃ§a nÃ£o substitui as licenÃ§as dos componentes de terceiros.
Os avisos completos e o inventÃ¡rio de versÃµes estÃ£o em `_internal/licenses`
no pacote Windows. Os modelos de IA nÃ£o acompanham o pacote.

## Fontes correspondentes

Cada release binÃ¡ria deve disponibilizar, na mesma pÃ¡gina de download:

- cÃ³digo do JARVIS e seus scripts de build, na tag da release;
- `JARVIS-Fontes-Terceiros-0.1.2-public1.zip`, com os arquivos de fonte,
  receitas/patches do fornecedor e manifesto SHA-256;
- `SHA256SUMS.txt`, com os hashes dos downloads publicados.

O lock [sources.lock.json](packaging/windows/sources.lock.json) fixa URLs,
versÃµes/commits e hashes dos 35 arquivos de fonte. Para reobter esses mesmos
arquivos, use `scripts/prepare_windows_sources.py --plan
packaging/windows/sources.lock.json --output data/third-party-sources`.
Esse script apenas baixa e verifica arquivos: nÃ£o executa cÃ³digo de terceiros.

Os fontes abrangem Piper 1.7.0 e o commit espeak-ng usado por seu CMake,
PySide/Shiboken 6.11.2 e os mÃ³dulos Qt usados, PyAV 16.1.0, FFmpeg 8.0.1,
as receitas/patches pyav-ffmpeg 8.0.1-3 e suas dependÃªncias. Quando o servidor
de um tarball estava indisponÃ­vel, o lock usa o commit da mesma versÃ£o no
repositÃ³rio oficial e preserva o hash do tarball original como referÃªncia.
Para snapshots Git, siga as instruÃ§Ãµes upstream de bootstrap/autogen antes do build.

## CondiÃ§Ãµes preservadas

- Piper Ã© GPL-3.0-or-later; seus avisos e fontes completos acompanham a release.
- Qt/PySide usam as licenÃ§as abertas aplicÃ¡veis aos mÃ³dulos. A ligaÃ§Ã£o Ã© dinÃ¢mica;
  substituiÃ§Ã£o/relink e engenharia reversa para depuraÃ§Ã£o de modificaÃ§Ãµes sÃ£o permitidos.
  Nenhuma assinatura ou bloqueio prÃ³prio do JARVIS impede bibliotecas modificadas.
- FFmpeg e codecs conservam suas licenÃ§as. O wheel inclui bibliotecas x264/x265;
  o texto LGPL retornado por uma API nÃ£o substitui a anÃ¡lise dos componentes.
  Os respectivos fontes e os patches do fornecedor foram preservados no kit.
- Demais dependÃªncias conservam seus avisos BSD/MIT/Apache e demais termos,
  conforme inventÃ¡rio e arquivos de licenÃ§a do pacote.
- CUDA/driver, Ollama e modelos sÃ£o prÃ©-requisitos separados e nÃ£o foram copiados
  do computador do autor para a distribuiÃ§Ã£o. Cada um conserva seus prÃ³prios termos.

As instruÃ§Ãµes de reproduÃ§Ã£o estÃ£o em [Windows](docs/manual/WINDOWS_EXECUTAVEL.md)
e [dependÃªncias fixadas](requirements/windows-py313/README.md).
