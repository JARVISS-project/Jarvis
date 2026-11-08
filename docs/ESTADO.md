# Estado da edição pública

O JARVIS é uma prévia para Windows x64, com configuração individual e integrações opcionais.
O núcleo local é utilizável após preparar Ollama/modelo e permissões. O fluxo de
instalar e conectar Google sem preparação técnica ainda não está concluído.

Veja o [relatório completo de funcionalidades, correções e prioridades](RELATORIO_ESTADO.md)
e os [resultados estruturados da revisão](REVISAO_VALIDACAO.json).

## Revisão 0.1.3-public2

- Fluxo de agenda de consulta separado das opções de criar/alterar eventos.
- Preparação inicial desbloqueada, consulta explícita da agenda principal e seleção individual.
- Tutorial com orientação para conversa local; detalhes técnicos recolhidos.
- Layout de contas e primeiro uso com melhor contraste e controles agrupados.
- Estado não verificado separado de indisponibilidade comprovada.

## O que continua pendente

- Cliente OAuth público do aplicativo para um login Google simples, sem JSON manual.
- Validação em Windows limpo e jornada Google real de uma instalação pública nova.
- Seleção guiada de modelo, ativação de eventos pela interface e reinício orientado.
- Voz CUDA, compatibilidade ampliada e revisão de dependências opcionais.

A evidência anterior da prévia 0.1.2 está em [VALIDACAO.json](VALIDACAO.json).
Voz CPU/Piper e conversa local foram testadas; isso não homologa todo equipamento.
Instalador não inclui modelos, perfis ou credenciais de outra pessoa e não tem assinatura paga.
