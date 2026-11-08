# Estado da ediÃ§Ã£o pÃºblica

PrÃ©via para Windows x64, configuraÃ§Ã£o individual e integraÃ§Ãµes opcionais.
O programa Ã© gratuito e GPL-3.0-or-later. Modelos sÃ£o preparados separadamente.
As notas da versÃ£o informam os testes realizados e suas limitaÃ§Ãµes.

Para ampliar a validaÃ§Ã£o:

- Testar instalaÃ§Ã£o/primeira configuraÃ§Ã£o em Windows limpo.
- Melhorar a orientaÃ§Ã£o para preparar Ollama, modelo local e voz.
- Resolver e validar dependÃªncias CUDA em hardware compatÃ­vel.
- Avaliar precisÃ£o e desempenho da voz em mais equipamentos.

Essas pendÃªncias nÃ£o significam que toda integraÃ§Ã£o estÃ¡ validada. Uma sondagem
de hardware nÃ£o comprova inferÃªncia, precisÃ£o ou compatibilidade universal.

## Prévia 0.1.2-public1

2502 testes passaram, 1 skip por symlink indisponível, zero falhas.
Instalador: instalação/atualização, 2388 hashes, Qt, reinstalação e
desinstalação com perfil temporário preservado. Portátil: Qt Windows, conversa
Qwen local, Piper/WAV e STT CPU/int8 com áudio sintético, sem microfone/playback.
Perfil novo sem notas/contas, nome vazio e tratamento natural; preferências
existentes preservadas. [Resultados estruturados](VALIDACAO.json).

A suíte pública cobre o produto e segurança. Ferramentas privadas de gestão
e testes de antigos corpora pessoais não fazem parte desta edição.

O pacote público atualiza certifi para 2026.10.18 e filelock para 4.2.1,
com hashes oficiais e ambiente independente. Dependências opcionais de voz
alternativa e ferramentas de desenvolvimento ainda precisam de revisão; os
alertas antigos do GitHub também abrangiam componentes fora desta edição.
Testes funcionais não substituem a revisão de vulnerabilidades conhecidas.

A seleção guiada do modelo local na primeira abertura permanece no backlog.
Nesta prévia, prepare no Ollama o modelo inicial informado no manual.
