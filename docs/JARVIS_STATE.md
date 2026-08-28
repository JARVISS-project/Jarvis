# JARVIS — Estado Atual do Projeto

> Documento vivo de continuidade técnica. O Git continua sendo a fonte de verdade do código; este arquivo registra decisões, arquitetura e marcos conhecidos.

Última consolidação: **28/08/2026**

## 1. Objetivo

O JARVIS é um **agente pessoal local**, não apenas um chatbot. A direção de longo prazo é atuar como uma camada inteligente sobre o computador, com voz, visão, memória, Skills, automação de desktop, navegador e acesso remoto seguro.

## 2. Regra arquitetural central

> **O LLM interpreta. O código autoriza. A Tool executa. A Skill lembra o procedimento.**

O LLM não recebe shell/PowerShell irrestrito nem acesso direto ao sistema.

```text
Usuário
  ↓
Intent / LLM / Fast Path / Skill
  ↓
Tool
  ↓
ToolExecutor
  ↓
Capability / Policy
  ↓
SafetyManager
  ↓
RiskLevel / confirmação
  ↓
Execução
  ↓
Audit / verificação
  ↓
Resposta
```

Regra de grounding: o JARVIS não deve afirmar que criou, abriu, apagou, instalou, escreveu, listou ou concluiu uma ação real sem execução comprovada.

Política de autonomia: `SAFE`, `LOW`, `MEDIUM` e `HIGH` são permitidos sem confirmação por risco. Somente `CRITICAL` exige confirmação explícita. Capabilities, guards, preflight, confirmação de identidade de alvo, kill switch e audit continuam independentes dessa fronteira.

## 3. Ambiente principal

- Windows
- Python 3.13.x
- Ryzen 5 4500
- 16 GB RAM
- RTX 3060 12 GB
- NVMe 1 TB
- repositório local: `C:\Users\kayna\Documents\Jarvis`
- branch principal: `main`
- remoto: `https://github.com/JARVISS-project/Jarvis.git`

## 4. LLM

Runtime local: **Ollama**.

Modelo principal:

```text
qwen3:4b-instruct-2507-q4_K_M
```

Decisão: manter este modelo até que benchmarks reais indiquem necessidade de troca.

## 5. Voz / STT / TTS

STT:

- `faster-whisper` / CTranslate2;
- Whisper `medium` em CUDA no modo normal;
- warm-up antes do loop de wake word.

TTS principal:

- Piper pt-BR Razo;
- `fast=1.00`;
- `natural=1.18`;
- `calm=1.30`;
- pausa entre sentenças: `0.18s`;
- cauda de silêncio: `0.30s`;
- `natural` é o padrão.

Chatterbox continua disponível como backend neural/qualidade em processo separado.

O modo de voz permanece half-duplex por padrão até existir cancelamento acústico de eco confiável.

## 6. Wake word

A experiência apresenta a chamada como **“Jarvis”**. O detector atual ainda deriva do modelo `hey_jarvis` do openWakeWord; sensibilidade e falsos positivos precisam continuar sendo avaliados em uso real.

## 7. Grounding e linguagem natural

O projeto possui Fast Paths para comandos determinísticos e uma segunda tentativa controlada de Tool quando o pedido é claramente operacional, mas o LLM responde apenas em texto.

Existe `ExecutionClaimGuard` para bloquear alegações de execução sem Tool real.

Princípio de UX: o usuário não deve precisar aprender frases rígidas de parser. O JARVIS deve interpretar variações naturais sem reduzir segurança.

### Direção conversacional e goal-driven

O JARVIS evolui de um agente predominantemente command-driven para um agente conversacional, orientado a objetivos e progressivamente proativo. O modelo pode conversar, avaliar alternativas, planejar, sugerir caminhos, assumir uma posição quando solicitado e discordar quando houver motivo concreto. Deve distinguir fatos, inferências e recomendações, sem fingir sentimentos, experiências pessoais ou conhecimento que não possui.

A separação de responsabilidades permanece absoluta:

```text
LLM    → conversa, interpreta, avalia, planeja e sugere
Policy → autoriza efeitos reais
Tool   → executa
Audit  → registra
```

Planejar ou sugerir não autoriza execução. A primeira fundação implementada é uma classificação determinística, anterior aos Fast Paths, entre `ADVISORY` e `EFFECT_REQUEST`. Turnos consultivos não recebem schemas de Tools, não acionam retry operacional e continuam usando uma única chamada ao LLM. Pedidos explícitos de efeito preservam o fluxo atual.

Direção de iniciativa futura, independente de risco e capabilities:

- `CONSERVATIVE`: responde ou executa somente o pedido;
- `BALANCED`: responde e pode oferecer uma sugestão adicional relevante, sem executar a sugestão; é a direção padrão;
- `PROACTIVE`: poderá tomar iniciativas somente dentro de capabilities e mandatos explicitamente autorizados.

O nível de iniciativa nunca deve conceder autorização, elevar capability, ignorar confirmação, alterar risco ou contornar o kill switch.

### Confirmação crítica (implementada)

A sessão de confirmação crítica (`ConfirmationSessionManager`) está implementada em `src/jarvis/security/confirmation_session.py` e integrada ao `Harness` via `confirmation_sessions`. O fluxo segue o estado observável:

```text
REQUEST_CONFIRMATION → WAITING_CONFIRMATION
                     → CONFIRMED | DENIED | EXPIRED
```

- Uma única sessão ativa por vez (fail-closed se já existir).
- `PreparedInvocation` registra tool, alvo, argumentos (digest SHA-256), risco e `interaction_id`.
- Reminders determinísticos, sem LLM: primeiro entre 20–30s, intervalo 30s, máx. 2 reminders, expiração ~120s (configurável).
- `decide()` exige `session_id` e `challenge_id` coincidentes; respostas tardias/fora de contexto → `STALE`.
- Concorrência: confirmação e expiração são *single winner* (RLock + Condition).
- Limpeza atômica: `cleanup` callback remove grants e zera `_active` antes de notificar.
- Scheduler em thread daemon; não bloqueia o runtime.
- Testes: `tests/security/test_confirmation_session.py` (unitário) e `tests/integration/test_critical_confirmation_session.py` (integração).

### Memória futura

A evolução de memória deve separar:

- semântica: fatos e preferências explicitamente autorizados, recuperados por relevância;
- episódica: eventos resumidos, com provenance, sensibilidade e retenção limitada;
- procedural: Skills estruturadas, sempre executadas pelo `ToolExecutor`.

Memória nunca concede autorização e não deve ser recuperada integralmente em toda mensagem. Recuperação pesada e persistência episódica automática não fazem parte do primeiro sprint.

## 8. Obsidian

A integração é local e usa o Vault diretamente, com guard próprio. Tools cobrem list/search/read/open/create/append/delete note. Operações destrutivas continuam sujeitas a risco, guards, capability e audit; confirmação por risco ocorre somente em `CRITICAL`.

Ambiguidades de nome devem ser perguntadas, não adivinhadas.

## 9. Skills

Fundação atual em `src/jarvis/skills/` com modelo/store/learner/executor/matcher/mirror.

- Tool = capacidade.
- Skill = procedimento estruturado que usa Tools.
- Skill não reduz risco.
- Markdown do Obsidian não é fonte executável.

## 10. Application Layer e Ambient UI

A interface está desacoplada do Core.

```text
VoicePipeline
    ↓
VoiceStateBridge
    ↓
AgentStateBus
    ↓
LocalStateServer
    ↓ 127.0.0.1
Ambient UI
```

Implementado:

- estado observável na camada `application`;
- bridge do VoicePipeline;
- IPC local em loopback;
- canal visual somente Core → UI;
- detalhes sensíveis fora do IPC visual;
- PySide6 separado do Core;
- Ambient HUD com animações por estado;
- painel expansível;
- system tray;
- `python -m jarvis.ui` para simulação;
- `python -m jarvis --voice --ui` para modo live;
- `python -m jarvis.ui --live` para reabrir a UI;
- fechar a HUD não deve matar o Runtime.

Estados visuais incluem `WAITING_WAKE`, `LISTENING`, `PROCESSING`, `SPEAKING`, `WAITING_FOLLOWUP`, `WAITING_REPAIR`, `CONFIRMATION`, `EXECUTING` e `ERROR`.

A partir da fundação de Desktop Automation, `EXECUTING` deixou de existir apenas no modo de demonstração: um observador do `ToolExecutor` publica esse estado **somente depois da autorização e imediatamente antes da execução real**. Ao terminar, volta para `PROCESSING`; exceções de runtime publicam `ERROR`. O observador recebe somente o nome da Tool e o resultado estruturado, sem argumentos/alvos sensíveis.

### Indicadores visuais ainda não são telemetria

Textos como:

```text
VOICE 01   CORE 01   TOOLS 01   SAFETY 01
● LOCAL LINK   ● POLICY LAYER   ● AUDIT
```

são **decorativos atualmente**. Não devem ser interpretados como evidência de saúde operacional. Futuramente precisam receber telemetria real ou ser marcados de modo inequivocamente decorativo.

## 11. Segurança da UI

A UI não possui canal de comandos para o Core. O IPC atual é destinado à publicação de estado visual via loopback.

Qualquer ação real continua passando por Tool, policy, SafetyManager, risco/confirmação e audit.

## 12. Harness e coordenadores de aplicação

Dois sprints de decomposição do Harness foram concluídos sem mudança intencional de comportamento. `src/jarvis/harness.py` caiu de aproximadamente **2100 para ~890 linhas**.

Responsabilidades extraídas para `src/jarvis/application/`:

```text
Harness
├── IntentCoordinator
├── AgentLoopCoordinator
├── SkillCoordinator
├── ObsidianCoordinator
├── ToolExecutionCoordinator
└── ResponseCoordinator
```

- `IntentCoordinator`: escolhe fast path, Skills, Obsidian, PendingAction ou agent loop e aplica a política de keep-alive;
- `AgentLoopCoordinator`: seleciona Tools expostas ao LLM, executa retries controlados, processa Tool Calls e compõe o ciclo LLM → Tool → LLM;
- `SkillCoordinator`: aprendizado, matching, execução e mirror de Skills;
- `ObsidianCoordinator`: disponibilidade e fast paths determinísticos do Obsidian;
- `ToolExecutionCoordinator`: resolução de alvo, PendingAction, autorização composta e contexto operacional;
- `ResponseCoordinator`: contexto do LLM, roteamento equivalente, firewall e grounding de resposta.

O `Harness` continua como maestro, dono do ciclo de vida, latência, trace e chamada primitiva `_execute_tool()`. Os coordenadores não executam Tools diretamente por fora desse caminho. Wrappers temporários permanecem para preservar compatibilidade com testes/código existente.

A decomposição atual já é suficiente para impedir que Desktop Automation, Vision e Browser precisem entrar como blocos gigantes no Harness. Antes desses subsistemas, ainda vale revisar as fronteiras públicas dos coordenadores e reduzir wrappers privados gradualmente, sem quebrar comportamento.

## 13. Desktop Automation Foundation

O primeiro estágio de Desktop Automation foi adicionado em módulo próprio, sem voltar a crescer o `Harness`. As capabilities `DESKTOP_READ` e `DESKTOP_CONTROL` já existiam e agora possuem Tools reais, ambas **desabilitadas por padrão** na configuração.

Arquitetura:

```text
LLM / Intent
  ↓
Desktop Tool
  ↓
ToolExecutor
  ↓
CapabilityPolicy
  ↓
SafetyManager / confirmação
  ↓
DesktopService
  ↓
WindowsDesktopBackend
```

Tools atuais:

- `desktop_list_windows` — LOW / `DESKTOP_READ`;
- `desktop_list_applications` — SAFE / `DESKTOP_READ`;
- `desktop_focus_window` — MEDIUM / `DESKTOP_CONTROL`;
- `desktop_open_application` — MEDIUM / `DESKTOP_CONTROL`;
- `desktop_move_pointer_to_window` — MEDIUM / `DESKTOP_CONTROL`;
- `desktop_press_escape` — MEDIUM / `DESKTOP_CONTROL`;
- `desktop_close_window` — HIGH / `DESKTOP_CONTROL`.

Abertura de aplicativo usa catálogo explícito. O LLM não fornece executável, argumentos, shell ou PowerShell. Catálogo inicial: `notepad`, `calculator`, `explorer`. Depois do lançamento, o backend exige, dentro de um prazo limitado, processo com identidade definida no catálogo e janela de topo visível ligada a esse processo e a uma classe Win32 permitida. Processo isolado ou janela de infraestrutura do shell não comprovam sucesso. Classes atuais: `Notepad`, `Windows.UI.Core.CoreWindow`/`ApplicationFrameWindow` e `CabinetWClass`; `Progman` não é aceito como evidência do Explorer. Sem as evidências, a Tool retorna erro; quando existem, a resposta informa apenas que a solicitação foi enviada e o estado visível foi verificado, sem alegar causalidade quando o aplicativo já estava aberto. Resolução de janela prefere título exato e só aceita match parcial quando é único; ambiguidades falham em vez de escolher arbitrariamente.

Pedidos explícitos de janela, como “foca a janela do Obsidian”, priorizam Desktop em vez da integração de notas. Um matcher pequeno cobre também formas naturais como “vai pro Obsidian”, “coloca o VS Code em foco” e “traz a calculadora pra frente”, encaminhando a Tool pelo fluxo normal de capability, policy, safety e audit sem chamada adicional ao LLM. Depois do kill switch, as Tools de controle permanecem roteáveis e devolvem diretamente o bloqueio estruturado, sem depender de paráfrase do LLM.

Referências como “traz ela de volta”, “foca nela” e “volta pra ela” usam somente a última consulta de janela que terminou com foco bem-sucedido. O contexto é efêmero, não autoriza a ação e não preserva um handle: a janela é resolvida novamente pelo título em cada pedido. Ausência de contexto, janela desaparecida ou novas ambiguidades falham sem adivinhação.

Controle possui `DesktopKillSwitch` fora do LLM. No Windows, quando `desktop_control` está habilitado, o runtime registra **Ctrl+Alt+K** por Win32. Falha no registro aciona o kill switch e deixa o controle fail-closed. Uma vez acionado, o LLM não possui Tool para resetá-lo; o rearmamento normal é feito reiniciando o runtime.

As ações iniciais (`focus/open`) são curtas. Qualquer sequência de mouse/teclado deve consultar o kill switch entre passos; não é aceitável ter uma automação que só verifique cancelamento antes de começar.

A fundação limitada de input não aceita coordenadas, cliques, texto, repetição, modificadores ou tecla escolhida pelo LLM. `desktop_move_pointer_to_window` restaura/foca uma janela inequívoca, calcula seu centro pelo retângulo Win32 e verifica a posição final do cursor. `desktop_press_escape` foca e verifica a janela, envia somente key-down/key-up de Escape via `SendInput`, libera a tecla em `finally` e verifica novamente o foreground. O kill switch é consultado entre as etapas; se disparar após key-down, key-up ainda é emitido para evitar tecla presa e nenhuma etapa adicional é executada.

`desktop_close_window` envia somente `WM_CLOSE` para uma janela inequívoca. Não usa `TerminateProcess`, não descarta alterações e não responde diálogos de salvamento. Se o handle desaparecer dentro do prazo, informa fechamento verificado; se continuar válido, informa apenas que a solicitação foi enviada e que a janela pode estar aguardando confirmação do aplicativo.

O estado visual `EXECUTING` é publicado somente após autorização e imediatamente antes da execução real. Sucesso restaura `PROCESSING`; erro de runtime ou recurso não encontrado durante a execução publica `ERROR`. O observador recebe somente o nome da Tool e o resultado estruturado, sem argumentos ou alvos sensíveis.

Teste de integração automatizado cobre `VoicePipeline` após STT, `Harness`, routing, `ToolExecutor`, capability, audit, `AgentStateBus` e IPC loopback da UI para as Tools limitadas de input. Os comandos determinísticos de centro do ponteiro e Escape usam `llm_calls=0`. Sucesso publica `EXECUTING -> PROCESSING`; falha de pós-verificação publica `EXECUTING -> ERROR` e não chama o LLM para reescrever o erro. O IPC não recebe detalhe, título ou argumentos. Esse teste não substitui aceite manual com microfone, TTS e processo Qt reais.

Falhas de abertura distinguem processo ausente de processo sem janela visível permitida. Foreground lock e janela ainda minimizada possuem mensagens específicas; nenhuma delas habilita bypass do Windows.

Configuração local:

```text
/config desktop
/config desktop read on|off
/config desktop control on|off
```

Ainda não implementado neste estágio: coordenadas arbitrárias, clique, digitação de texto, atalhos genéricos, persistência/exposição de screenshots, interpretação visual multimodal, automação de navegador ou execução de comandos.

Validação no Windows real em 28/08/2026:

- `calculator`, `notepad` e `explorer` apresentaram processo e janela com classe esperada;
- foco real do Explorer foi aceito e executado;
- duplicidade de título da Calculadora foi recusada em vez de escolher uma janela;
- pressionamento físico de Ctrl+Alt+K acionou o listener e uma tentativa subsequente de mover o ponteiro retornou `security_blocked` sem alterar a posição;
- ponteiro foi movido para o centro restaurado do Explorer e a posição final foi verificada;
- Escape foi aceito pelo `SendInput` em um Notepad colocado em foreground pelo usuário, com foreground final verificado;
- foreground lock do Windows continua sendo respeitado; o JARVIS não usa bypass quando `SetForegroundWindow` é recusado.

Aceite live com `python -m jarvis --voice --ui`:

- wake word, STT, Piper, Ollama, HUD e kill switch iniciaram no runtime completo;
- movimento para o centro do Explorer e “mova novamente” executaram deterministicamente com verificação;
- Escape canônico no Notepad executou e verificou foreground;
- fechamento cooperativo do Notepad executou por voz e verificou a ausência da janela;
- variantes STT observadas (`movam`, `centro da tela`, `botão Escape`, artigos e preposições) foram incorporadas ao matcher pequeno;
- pedido de Escape sem janela agora solicita alvo explícito em vez de inferir;
- alegações sem Tool como “Escape pressionada”, “ponteiro na janela” e “botão visível” foram adicionadas ao grounding;
- `av 18.1.0` foi bloqueado por Windows Application Control no `.venv`; `av 16.1.0` carregou corretamente e o extra `voice` passou a declarar `av>=11,<17`.

### Vision Foundation read-only

A capability `VISION` permanece desabilitada por padrão e independente de `DESKTOP_READ`/`DESKTOP_CONTROL`. Configuração local: `/config vision on|off` ou `JARVIS_VISION`.

`vision_observe_screen_change` é MEDIUM, não recebe argumentos e usa Pillow somente para captura local em memória. O serviço valida formato RGB, consistência do buffer e limite de 33.177.600 pixels, serializa observações concorrentes e mantém apenas fingerprint SHA-256 interno da última captura. A Tool retorna somente `baseline`, `changed` ou `unchanged` e dimensões; pixels e digest não entram em resposta, audit ou IPC e nenhum arquivo de imagem é criado.

O routing é determinístico (`llm_calls=0`). Capability é verificada pelo `ToolExecutor` e novamente no `VisionService`. Testes cobrem bloqueio antes da captura, frames inválidos, limite, privacidade do resultado, audit com alvo fixo, registro condicional da Tool e ausência de raw output.

Validação real do backend observou uma área multi-monitor de `3840x3240`; duas capturas produziram baseline/mudança e o diretório temporário continha somente banco e audit, sem screenshot.

Quando `VISION` está habilitada, o `ToolExecutor` acrescenta verificação visual opcional às Tools Desktop somente depois da autorização: captura baseline, executa a Tool e captura o pós-estado. A baseline usa token UUID single-use, digest da identidade lógica do alvo e expiração de 10 segundos; mismatch, expiração, replay ou falha da Tool cancelam/invalidam o token. Falha do verificador não bloqueia, autoriza, promove nem transforma o resultado da Tool.

O sinal interno `visual_change` contém somente `changed`, `unchanged` ou `None`. Ele não altera a resposta ao usuário, status, grounding, audit ou IPC. Mudança visual é evidência adicional temporal, nunca prova semântica de que a ação causou o estado observado. A identidade atual deriva do alvo lógico resolvido; binding futuro a handle/processo preparado ainda é necessário para eliminar a janela de TOCTOU entre preflight e execução.

Validação Win32 real com Explorer em foreground produziu Tool `success`, posição verificada e `visual_change=changed`. Uma tentativa anterior bloqueada pelo foreground lock cancelou a baseline e não gerou sinal visual falso.

## 14. Testes

Marco conhecido após a revisão de autonomia e hardening de Desktop Automation:

```text
766 passed, 1 skipped
compileall OK
```

A suíte usa fakes nas fronteiras externas e não substitui teste real de voz, Ollama ou Qt. As validações Win32 reais conhecidas estão registradas na seção de Desktop Automation.

Sempre validar no PC principal:

```powershell
git status
python -m pytest
```

## 15. Arquivos de continuidade

- `AGENTS.md`: regras do repositório; deve existir na raiz.
- `docs/JARVIS_STATE.md`: documento vivo oficial de estado.
- patches, dumps e arquivos locais de recovery na raiz devem permanecer ignorados pelo Git.

## 16. Próximas prioridades

Próximo sprint recomendado: **Vision Target-Bound Verification**, mantendo clique e texto bloqueados.

1. preparar e vincular handle/processo da janela uma única vez para preflight, ação e pós-verificação;
2. reduzir comparação de tela inteira para região da janela preparada, sem persistir pixels;
3. manter mudança visual como sinal adicional, nunca autorização ou prova semântica isolada;
4. evoluir memória pessoal persistente com autorização clara;
5. transformar indicadores decorativos da HUD em telemetria real;
6. Vision para percepção e verificação da tela;
7. browser agent;
8. startup automático e background runtime;
9. acesso remoto seguro.

### Fase futura: Voice & Persona Evolution

Esta fase permanece no roadmap e não faz parte do sprint atual de Desktop Reliability + Agent Autonomy.

- Evoluir a personalidade do JARVIS para uma identidade mais elegante, inteligente, confiante e natural, com humor seco e sarcasmo moderado e contextual.
- Permitir opiniões fundamentadas e discordância quando houver motivo concreto, sem humor em situações críticas, erros graves ou alertas.
- Manter personalidade e engine de TTS como responsabilidades separadas.
- Criar futuramente `SpeechStyle`, ou mecanismo equivalente, para adaptar ritmo e expressividade ao contexto.
- Antes de substituir qualquer engine existente, executar benchmark local de TTS e clonagem autorizada, comparando inicialmente Chatterbox Multilingual e Qwen3-TTS local.
- Preservar Piper como fallback rápido e offline; não treinar modelo do zero inicialmente e priorizar clonagem zero-shot/local.
- Usar um conjunto fixo de frases para medir naturalidade, sarcasmo, execução, alerta e conversa.
- Medir qualidade, latência, VRAM e estabilidade no hardware atual.
- Cachear frases frequentes, como wake acknowledgement e confirmações curtas.
- Não remover o pipeline de voz atual antes de existir comparação objetiva e teste real.

## 17. Regras para continuidade

Antes de alterar código:

1. ler `AGENTS.md`;
2. ler `docs/JARVIS_STATE.md`;
3. ler os arquivos reais relevantes;
4. verificar `git status`;
5. não assumir working tree limpo;
6. preferir patches contra o estado real mais recente;
7. executar `git apply --check` antes de aplicar;
8. executar testes;
9. não fazer commit, push, reset ou restore sem autorização explícita.
