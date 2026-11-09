# Estado da edição pública

O JARVIS é uma prévia para Windows x64, com configuração individual e integrações opcionais.
O núcleo local é utilizável após preparar Ollama/modelo e permissões. O fluxo de
instalar e conectar Google sem preparação técnica ainda não está concluído.

Veja o [relatório completo de funcionalidades, correções e prioridades](RELATORIO_ESTADO.md)
e os [resultados estruturados da revisão](REVISAO_VALIDACAO.json).

## Google — atualização de 9 de novembro de 2026

Projeto em produção, sem verificação aprovada. A análise da marca aguarda o
reconhecimento da propriedade do site; o Google pediu esperar 24 horas após a
comprovação. O Search Console confirma o proprietário. A revisão de acesso aos
dados permanece desabilitada até verificar/publicar a marca.
[Preparação e pendências](GOOGLE_VERIFICACAO.md).

## Prévia 0.1.4-preview1 — evidências de 8 de novembro

- Login Google de consultas incluído no pacote; ainda restrito às contas de teste.
- Login real, persistência e consulta natural validados com a conta de suporte.
- Página e privacidade publicadas; propriedade do endereço do site confirmada.
- 2.545 testes aprovados, 1 ignorado; instalador com reinstalação e remoção validados.
- [Relatório e limites atuais](GOOGLE_PUBLIC_PREVIEW.md) · [Validação](GOOGLE_PUBLIC_VALIDACAO.json).

## Revisão 0.1.3-public2

- Fluxo de agenda de consulta separado das opções de criar/alterar eventos.
- Preparação inicial desbloqueada, consulta explícita da agenda principal e seleção individual.
- Tutorial com orientação para conversa local; detalhes técnicos recolhidos.
- Layout de contas e primeiro uso com melhor contraste e controles agrupados.
- Estado não verificado separado de indisponibilidade comprovada.

## O que continua pendente

- Verificação da marca e dos acessos Google; validação do login público após aprovação.
- Validação em Windows limpo e jornada Google após a liberação pública.
- Seleção guiada de modelo, ativação de eventos pela interface e reinício orientado.
- Voz CUDA, compatibilidade ampliada e revisão de dependências opcionais.

A evidência anterior da prévia 0.1.2 está em [VALIDACAO.json](VALIDACAO.json).
Voz CPU/Piper e conversa local foram testadas; isso não homologa todo equipamento.
Instalador não inclui modelos, perfis ou credenciais de outra pessoa e não tem assinatura paga.
