# Preparação da verificação Google

Estado conferido em **9 de novembro de 2026**. O projeto JARVIS Public está
**em produção**, após autorização explícita do mantenedor. Isso é o status de
publicação, não uma aprovação da marca nem dos acessos a dados.

## Pendência atual e sequência

A primeira análise da marca informou que a propriedade da página inicial ainda
não foi reconhecida e pediu aguardar 24 horas após a comprovação. O Search
Console confirma um proprietário verificado para o endereço exato da página.
Não foi afirmado no formulário que o problema já está corrigido nem contestada
a análise antes desse prazo.

1. Após o intervalo pedido pelo Google, conferir novamente a marca. A existência
   da propriedade de prefixo de URL não garante aprovação de todos os requisitos
   de domínio. Se o aviso persistir, investigar o requisito apontado antes de
   reenviar ou pedir outra análise.
2. Verificar/publicar a marca. A central mantém **Prepare for verification**
   desabilitado para acesso a dados enquanto essa etapa não é concluída.
3. Conferir os acessos mínimos, terminar o vídeo real e revisar as respostas
   antes de enviar a solicitação de acesso a dados.
4. Após aprovação, validar uma instalação individual e uma conta que não estava
   na lista de teste. Só então anunciar a jornada pública como homologada.

Não houve envio da revisão de acesso a dados nesta etapa. O limite mostrado
pelo Google é de 100 usuários ao longo da vida do projeto enquanto os acessos
sensíveis não forem aprovados; o contador observado é 2. Não é uma garantia de
que qualquer conta ou organização conseguirá entrar.

## Acessos do login incluído

O pacote inclui somente a operação `calendar_read`. A tabela corresponde ao
código atual, não aos recursos planejados do programa.

| Acesso | Uso implementado | Limite |
|---|---|---|
| `openid` | Obter o identificador `sub` e vincular a conexão e os recursos à mesma conta. | Não solicita perfil nem e-mail. |
| `calendar.events.readonly` | Consultar eventos da agenda selecionada para responder perguntas de período. | Somente leitura; requer seleção, ativação e política permitida. |
| `calendar.calendars.readonly` | Consultar nome e fuso da agenda principal na preparação e fuso da agenda selecionada em execução. | Não lista automaticamente todas as agendas nem pede ACL. |

O consentimento Google não limita tecnicamente esses escopos a uma só agenda;
a restrição à seleção do usuário é aplicada também pelo programa. Não confundir
essa proteção local com uma concessão por recurso feita pelo Google.

## Rascunho das justificativas para o formulário

As respostas abaixo estão preparadas em inglês, **ainda não enviadas**. Devem
ser conferidas contra o formulário e a demonstração final.

### Application purpose

JARVIS is a free, open-source Windows desktop personal assistant. Its Google
Calendar integration answers a user's explicit schedule questions using a
calendar that the user connects, selects and enables. Each installation has
its own local profile and OAuth grants. The maintainer does not receive users'
calendar content or tokens on an application server.

### openid

The application retrieves the authenticated account's subject identifier and
binds the local connection and selected resources to that verified identity.
This prevents a saved calendar selection from silently being reused with a
different Google account. The application does not request email or profile
scopes for this operation.

### calendar.events.readonly

JARVIS reads event titles and times for an explicitly selected, permitted
calendar to answer questions such as today's schedule, tomorrow's schedule or
the next seven days. Requests are bounded by the requested period and result
limit. A free/busy-only grant cannot provide the event details used in the
answer. The integration does not create, change or delete events with this
grant. The broader calendar.readonly and all write scopes are not requested.

Before submission, resolve the comparison with events.owned.readonly: the read
backend can target a selected calendar accessible to the user, including a
shared calendar, but the existing real demonstration only used the support
account's primary calendar. Demonstrate that existing selected-calendar use
case if relying on it to justify events.readonly; otherwise evaluate narrowing
the scope with compatibility tests. Do not claim this comparison is complete.

### calendar.calendars.readonly

The setup action retrieves the primary calendar's identifier, title and time
zone so that the user can review and save the intended calendar. Operational
queries also retrieve the selected calendar's time zone to calculate the
requested local day correctly. Event-only and free/busy scopes do not authorize
the Calendars.get endpoint used by this flow. No CalendarList or ACL access is
requested. The user explicitly selects a calendar before operational reading.

### Data handling

OAuth tokens and the connection registry are protected locally with Windows
DPAPI. Other profile data is not necessarily encrypted. Calendar results can
appear in the local conversation; they are not automatically delegated to an
external AI provider. Google data is not sold, used for advertising or used by
the project to train general-purpose AI models. Users can disconnect locally
and revoke the grant in their Google Account connections page.

## Roteiro da demonstração real — vídeo pendente

Ainda não há vídeo novo gravado ou publicado. As evidências de validação da
prévia não substituem o vídeo de revisão. Usar somente a conta de suporte já
autorizada; não exibir a conta pessoal, senha, tokens, códigos de autorização,
perfil real ou outras janelas particulares. Mostrar o consentimento em inglês,
como pede o Google, e identificar os controles portugueses na narração/legenda.

| Trecho | Mostrar na gravação | O que comprova |
|---|---|---|
| 1 | Site, política de privacidade e JARVIS com perfil de demonstração individual. | Identidade e informação ao usuário. |
| 2 | Contas e conexões → Agenda → Conectar Google para consultas. | Login iniciado por ação explícita. |
| 3 | Escolha somente da conta de suporte e tela completa de consentimento em inglês, com os três acessos declarados. | Correspondência entre app, cliente e permissões. |
| 4 | Retorno ao app, teste da conexão e Consultar minha agenda principal. | Identidade e uso de metadados. |
| 5 | Seleção explícita, Salvar esta agenda, Usar a agenda salva nas consultas e reinício. | Preparação separada da ativação. |
| 6 | Perguntar Qual é minha agenda de hoje? e mostrar a resposta real. | Uso do acesso a eventos. |
| 7 | Estado de escrita desligada e caminho para desconectar/revogar. | Separação e controle. |

A conta de suporte estava vazia no teste anterior. Uma resposta vazia é válida,
mas não demonstra a renderização de um compromisso. Se for preciso criar um
evento sintético para a gravação, obter autorização específica antes de gravar
dados na conta; não inserir compromissos silenciosamente para melhorar o vídeo.
Não alegar que testes simulados ou uma sequência de telas equivalem a uma nova
validação real. Publicação do vídeo e envio final do formulário continuam pendentes.

## Evidência e referências

A prévia 0.1.4 e seus testes permanecem descritos em
[GOOGLE_PUBLIC_PREVIEW.md](GOOGLE_PUBLIC_PREVIEW.md). O executável não foi
recompilado nesta mudança de cadastro; ainda mostra o aviso local de teste.
As capturas de tela do navegador falharam por timeout; os estados foram
conferidos e registrados por acessibilidade em arquivos locais não publicados.
Nenhuma informação pessoal dos registros locais acompanha este documento.

Fontes oficiais consultadas:

- [Verificação de acessos sensíveis](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification).
- [Requisitos de verificação e demonstração](https://support.google.com/cloud/answer/13464321?hl=en).
- [Solução de problemas da página inicial](https://support.google.com/cloud/answer/13807376?hl=en).
- [Eventos: listar](https://developers.google.com/workspace/calendar/api/v3/reference/events/list).
- [Agendas: consultar metadados](https://developers.google.com/workspace/calendar/api/v3/reference/calendars/get).
