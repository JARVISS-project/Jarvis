# Conectar sua conta Google

## O que já existe e o que falta

Cada instalação guarda as próprias conexões. O JARVIS não inclui contas,
senhas, agendas ou tokens de outra pessoa. Consulta de agenda, criação de
eventos e documentos possuem concessões separadas.

**A release 0.1.3 ainda pede um arquivo OAuth Desktop.** A preparação da 0.1.4
permite incluir a identidade própria do JARVIS no pacote: nesse caso, clique
em **Conectar Google para consultas**, sem selecionar JSON. A tela informa
**Login JARVIS em teste**. O pacote não consulta o status remoto: em 9 de
novembro o projeto foi colocado em produção, mas marca e acessos ainda aguardam
verificação. O Google pode apresentar avisos e aplicar limites; não há
homologação geral do login. [Estado da revisão](../GOOGLE_VERIFICACAO.md).
Pacotes sem essa identidade
mantêm a escolha explícita de um cliente Desktop próprio.
Um cliente OAuth identifica o aplicativo; ele não substitui a conta e o
consentimento de quem vai usar. O fluxo Desktop é descrito pelo
[Google](https://developers.google.com/identity/protocols/oauth2/native-app).

## Preparação técnica atual

Para usar agora, prepare um projeto Google Cloud com Calendar API habilitada,
tela de consentimento configurada e cliente OAuth do tipo **Desktop app**.
Se o projeto estiver em teste, confira quais usuários podem acessá-lo.
Baixe o JSON do cliente. O [guia oficial de Calendar](https://developers.google.com/workspace/calendar/api/quickstart/python)
descreve essa preparação; não é necessário instalar Python para usar o JARVIS empacotado.

Não selecione JSON de conta de serviço nem cliente Web. Não compartilhe arquivos
de tokens, o perfil do JARVIS ou credenciais de uma instalação como solução.

## Consultar uma agenda

1. Abra **Contas e conexões → Agenda**.
2. Clique em **Conectar Google para consultas**. Se o pacote não tiver a identidade
   JARVIS, selecione seu JSON Desktop. O botão **Usar cliente Google próprio…**
   em Gerenciar contas mantém a opção de configuração independente.
   Faça o login e dê o consentimento no navegador oficial do Google.
3. Clique em **Consultar minha agenda principal**. Isso consulta nome e fuso;
   não seleciona automaticamente uma agenda, não lê eventos e não ativa recursos.
4. Escolha o nome retornado e clique em **Salvar esta agenda**.
5. Marque **Usar a agenda salva nas consultas** e reinicie o JARVIS.
6. Confira as permissões e faça o pedido em **Conversa**.

Abrir a tela com consultas desligadas não consulta o Google automaticamente.
Um bloqueio em Permissões continua valendo na preparação e no uso.
Se uma conexão existente ainda não foi verificada, use **Gerenciar contas → Testar**.

## Criar ou alterar eventos é opcional

Abra **Mostrar opções para criar ou alterar eventos** somente se precisar.
A autorização de eventos é explícita, separada da consulta. A seleção precisa
corresponder à mesma identidade e a uma agenda compatível com as restrições.
Salvar a agenda de eventos não altera a agenda de consultas nem ativa escrita.

A ativação avançada ainda é manual nesta prévia, com reinício. A interface
informa esse limite; o comando avançado existente é `/config google_calendar_write on`.
Isso não dispensa consentimento, seleção, permissões nem a confirmação de cada
operação. Priorize uma agenda própria de teste.

São suportados eventos simples e alterações limitadas de título/horário.
Convidados, recorrência, anexos, notificações, exclusão e agendas compartilhadas
fora das restrições não devem ser tratados como funcionalidades disponíveis.
Revisar um evento não o cria. A confirmação acontece na Conversa pelo executor.

## Documentos e outras contas

Documentos Google usam um fluxo separado de escolha explícita de arquivos,
com permissões mínimas. Não é acesso automático ao Drive inteiro.
Na configuração atual, ainda há preparação técnica do projeto e das APIs.
OpenAI/Gemini são opcionais, usam configuração própria e podem cobrar por API.

## Para um login público simples

O mantenedor precisa preparar a identidade OAuth do aplicativo, sua política de
privacidade e a distribuição do cliente apropriado. O Google pode exigir
verificação para os acessos solicitados. Isso precisa ser tratado antes de
anunciar integração Google pronta para qualquer pessoa. Consulte a
[orientação oficial de verificação](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification).

O cadastro separado JARVIS Public, suporte dedicado e Calendar API foram preparados.
A identidade incluída na prévia suporta somente `calendar_read`, com `openid`,
`calendar.events.readonly` e `calendar.calendars.readonly`. Escrita e documentos
não usam essa identidade automaticamente. Políticas e página pública estão em
`site/`; publicar as páginas não equivale a aprovação do Google.
