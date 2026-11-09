# Login Google — prévia 0.1.4

## Resultado e limite

A identidade do aplicativo pode ser incluída explicitamente no build. Nesse
pacote, **Conectar Google para consultas** inicia o login oficial sem pedir JSON.
O projeto Google está em **teste externo**: isso não libera o login para qualquer
conta. A 0.1.4-preview1 é uma prévia opcional; a prévia anterior é
0.1.3-public2. O Google ainda precisa concluir a revisão para acesso público.

Cada instalação continua sem contas conectadas. A identificação Desktop do
aplicativo não é um token de usuário e não dá acesso à conta do mantenedor.
O build aceita somente o projeto público separado, campos de registro Desktop
permitidos e endpoints oficiais. Perfis, tokens e outras propriedades são
recusados. A identidade não é descoberta no computador nem versionada no Git.

O login incluído permite apenas consultas de agenda, com `openid`,
`calendar.events.readonly` e `calendar.calendars.readonly`. A opção de cliente
próprio permanece disponível; escrita e documentos não usam automaticamente
esse cliente. Seleção de agenda, ativação, políticas e executor continuam
obrigatórios; conectar não autoriza escrita.

## Usabilidade

- O pacote identifica o login como **em teste**, sem prometer acesso universal.
- A seleção guiada de consultas usa o alias `principal`, correspondente às
  perguntas sem qualificador. Eventos continuam com seleção independente.
- Perguntas como “Qual é minha agenda de hoje?” são reconhecidas por padrões
  locais completos. Negações, alvos desconhecidos e duas intenções continuam
  bloqueados, sem delegar eventos a um modelo de IA.
- A tela principal usa o nome da conexão, preservando o identificador técnico
  para os detalhes de administração. O retorno do navegador orienta voltar ao
  JARVIS para verificar a conexão, em português.

## Páginas e cadastro Google

Página: https://jarviss-project.github.io/Jarvis/

Privacidade: https://jarviss-project.github.io/Jarvis/privacidade.html

As páginas são estáticas, sem scripts de analytics próprios, hospedadas no
GitHub Pages gratuito. Ambas foram verificadas por acesso sem autenticação e
conteúdo idêntico ao código revisado. A página inicial e a política foram
salvas no cadastro Google com o domínio `jarviss-project.github.io`.
Suporte e contato do desenvolvedor usam o e-mail dedicado do projeto.
A propriedade do endereço do site foi confirmada pelo Google Search Console
por tag HTML, com autorização do mantenedor. A tag não exibe o e-mail pessoal.

## Validação

O teste real autorizado usou exclusivamente a conta dedicada de suporte, num
perfil separado, com tokens locais protegidos pelo Windows. Login, identificação,
metadados da agenda, persistência após reinício e consulta de eventos foram
verificados. A agenda vazia retornou “Nenhum evento encontrado para hoje”.
Nenhum evento foi criado, alterado ou excluído; a conta pessoal não foi usada.

O teste também encontrou a diferença entre o alias guiado e o padrão das
perguntas, além de expressões naturais recusadas. Esses pontos foram corrigidos
com testes de aceitação e recusa. No executável final, a seleção foi salva pela
interface, o programa reiniciado e “Qual é minha agenda de hoje?” retornou a
agenda vazia da conta de suporte. O identificador técnico deixou o resumo
principal e um conflito do campo de evento com o dispatcher Qt foi corrigido.

- Regressão final: **2.545 aprovados, 1 ignorado histórico, zero falhas**.
- Revisão focada: 357 verificações; 33 verificações da interface após os últimos
  ajustes (grupos sobrepostos, não somar).
- Binário: 294 módulos próprios iguais ao fonte, 2.887 módulos examinados sem
  marcadores pessoais; nenhuma diferença de código.
- Instalador final: 2.389 arquivos com hashes iguais, reinstalação e remoção
  preservando o perfil de teste. Interface Qt e login incluído conferidos.
- Busca de segredos no código revisado: nenhuma ocorrência.
- Evidências consolidadas sem dados de contas em [GOOGLE_PUBLIC_VALIDACAO.json](GOOGLE_PUBLIC_VALIDACAO.json).

As primeiras capturas offscreen não renderizavam as fontes do Windows; a
conferência visual foi repetida com Qt nativo. Os testes iniciais também
detectaram expectativas antigas do nome interno e da exposição do identificador
na interface; foram atualizadas para o comportamento intencional, preservando
os testes de identidade, recusa e separação de escrita. Registros de tentativas
e perfis reais de validação permanecem locais, fora do código e do instalador.

## Ainda falta para liberar o Google para todos

Concluir o processo de revisão exigido pelo Google, incluindo demonstração
dos acessos solicitados, e validar a jornada com uma conta não cadastrada como
testadora após a liberação. A central Google atualmente informa que a verificação não é
exigida **enquanto o aplicativo está em teste**; isso não é aprovação pública.
Não houve promoção a produção nem envio do aplicativo para revisão nesta etapa.
Windows limpo, CUDA e escrita Google não foram homologados por este trabalho.

Referências oficiais: [fluxo Desktop](https://developers.google.com/identity/protocols/oauth2/native-app),
[verificação de acessos sensíveis](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification).
