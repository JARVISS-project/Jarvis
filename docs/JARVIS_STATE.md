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

### HUD Telemetry

Os indicadores textuais decorativos foram substituídos por telemetria agregada real do último turno:

```text
TURN 842ms   LLM 731ms/1
TOOLS 4ms/1   AUDIT OK / 17
```

Antes da primeira evidência, a HUD exibe `--`; não afirma mais `SAFE`, `POLICY LAYER`, link ativo ou audit saudável sem sinal correspondente. `TOOLS` conta invocações pelo Harness, não ações necessariamente concluídas. `AUDIT OK` significa somente que a última gravação local de audit terminou sem erro e o número representa gravações bem-sucedidas desde o início do runtime.

`TelemetryBus` é separado do `AgentStateBus` e contém somente inteiros limitados, booleanos e contadores: duração total/LLM/Tools, chamadas LLM/Tools e estado agregado de audit. Nomes de Tool, argumentos, alvos, paths, títulos, conteúdo, status, pixels, digests, handles e PIDs não entram no snapshot ou IPC.

O IPC continua Core → UI em loopback e protocolo v1, agora com mensagem aditiva `type=telemetry` e allowlist explícita. Publicação do Core apenas coloca payloads em uma fila limitada; uma thread daemon realiza os envios, clientes lentos são limitados e não bloqueiam Harness, Tool ou audit. Sequências regressivas de estado/telemetria são ignoradas pelo cliente. A geometria animada do HUD continua sendo linguagem visual, não indicador de saúde.

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

Quando `VISION` está habilitada, o `ToolExecutor` acrescenta verificação visual opcional às Tools Desktop somente depois da autorização: captura baseline, executa a Tool e captura o pós-estado. A baseline usa token UUID single-use, digest da identidade preparada do alvo e expiração de 10 segundos; mismatch, expiração, replay ou falha da Tool cancelam/invalidam o token. Falha do verificador não bloqueia, autoriza, promove nem transforma o resultado da Tool.

As Tools de janela agora resolvem o título uma única vez e criam um contexto efêmero vinculado a handle, processo e classe Win32. Preflight e cada etapa com efeito revalidam diretamente essa identidade, sem nova busca por título. Handle, PID, classe, região e digest não entram em resposta, audit, IPC, trace ou representação do contexto. Confirmações diferidas preservam somente um digest opaco da identidade preparada; após a confirmação, uma nova preparação com identidade diferente não consegue consumir o grant, mesmo que o título público permaneça igual.

A região da janela é lida depois da autorização e imediatamente antes da baseline. Pillow captura somente esse retângulo, inclusive com coordenadas negativas de múltiplos monitores, e o token obriga o pós-estado a reutilizar exatamente a região original. Dimensões retornadas pelo backend precisam corresponder ao retângulo solicitado. `desktop_open_application` não usa fallback de tela inteira porque não existe janela preparada antes do lançamento; seu sucesso continua dependendo da verificação semântica de processo e janela do `DesktopService`.

O sinal interno `visual_change` contém somente `changed`, `unchanged` ou `None`. Ele não altera a resposta ao usuário, status, grounding, audit ou IPC. Mudança visual é evidência adicional temporal, nunca prova semântica de que a ação causou o estado observado. Binding por handle, PID e classe reduz o TOCTOU atual; process creation time permanece uma opção de hardening futuro contra reutilização extrema de identidade no mesmo processo/classe.

Validação Win32 real com Explorer em foreground produziu Tool `success`, posição verificada e `visual_change=changed`. Uma tentativa anterior bloqueada pelo foreground lock cancelou a baseline e não gerou sinal visual falso.

### Persistent Personal Memory Foundation

A capacidade de memória pessoal persistente foi implementada com consentimento explícito, provenance, sensibilidade, retenção e exclusão controlados pelo usuário.

`MemoryEntry` agora inclui:
- `sensitivity`: `public`, `private`, `sensitive`;
- `provenance`: `user_explicit`, `conversation_derived`, `tool_observation`;
- `retention_days`: retenção opcional em dias;
- `consent_given`: consentimento explícito para uso no contexto do LLM;
- `metadata`: dicionário extensível para futuros atributos.

`MemoryStore` ganhou `get_relevant(query, max_results, min_score)` para recuperação por relevância semântica simples (Jaccard sobre tokens normalizados ≥3 chars) em vez de injetar o acervo completo. A normalização remove acentos para preservar consultas em português. `cleanup_expired()` remove fisicamente entradas vencidas na inicialização, listagem ou recuperação. `update_consent(id, bool)` permite conceder/revogar consentimento. Bancos SQLite anteriores são migrados sem descartar memórias existentes.

Comandos de CLI atualizados:
- `/remember <texto> [--categoria <cat>] [--sensibilidade <public|private|sensitive>] [--retencao <dias>] [--metadados <json>]`;
- `/memories` lista com sensibilidade e retenção;
- `/consent grant|revoke|list <id>` gerencia consentimento por memória;
- `/forget <id>` remove memória.

`ResponseCoordinator.system_context()` agora recebe `user_message` opcional e injeta apenas memórias relevantes (com consentimento) via `memory_store.get_relevant(user_message)`. Turnos sem memórias relevantes não recebem bloco de memória no system prompt.

Memória nunca concede autorização: `consent_given` controla apenas inclusão no contexto do LLM; execução continua passando por Tool, policy, SafetyManager, risco/confirmação e audit. Skills permanecem como memória procedural estruturada executada via `ToolExecutor`.

## 14. Testes

Marco conhecido após a conclusão de Google Integration Foundation:

```text
1004 passed, 1 skipped
compileall OK
git diff --check OK
```

A suíte usa fakes nas fronteiras externas e não substitui teste real de voz, Ollama ou Qt. O Remote Gateway possui integrações TCP e WebSocket loopback reais automatizadas; o Web Control Center possui smoke live com cliente TypeScript, Gateway e Core fake pela `RemoteCorePort`. As validações Win32 reais conhecidas estão registradas nas seções de Desktop Automation, Vision e Control Center.

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

Próximo sprint recomendado: **Google Drive Read-only Foundation**, somente após decisão explícita de sprint.

1. Google Drive Read-only Foundation;
2. hardening futuro de recovery/supervisão, sem restart infinito.

## 17. Control Center Foundation (concluído)

A fundação da interface de controle (Control Center) foi implementada, expandindo a Ambient HUD existente com um painel de abas dedicado à visualização agregada de telemetria, memória, skills e visão.

**Novas capacidades:**

- **Aba Telemetria**: Exibe métricas agregadas do último turno (TURN, LLM, TOOLS) e saúde do audit (AUDIT OK/ERROR), substituindo os indicadores decorativos anteriores por dados reais.
- **Aba Memória**: Lista memórias persistentes com sensibilidade (public/private/sensitive), consentimento (SIM/NÃO), retenção e ID. Atualiza automaticamente quando memórias são adicionadas/removidas.
- **Abas Skills**: Mostra skills aprendidas com contadores de execução/sucesso, triggers e IDs.
- **Aba Visão**: Status da capacidade Vision (habilitada/desabilitada) e última observação visual.

**Arquitetura:**

- `ControlCenterBus`: Barramento thread-safe para snapshots agregados (`ControlCenterSnapshot`) contendo telemetria, memória, skills e visão.
- `ControlCenterCoordinator`: Agrega dados de `MemoryStore`, `SkillStore`, `VisionService` e `TelemetryBus`, publicando no bus periodicamente e sob demanda.
- `AmbientWindow` (Qt): Painel expandido com `QTabWidget` contendo abas TELEMETRY, MEMORY, SKILLS, VISION. Assina `ControlCenterBus`, `AgentStateBus` e `TelemetryBus` via relays Qt.

**Novos arquivos:**

- `src/jarvis/ui/control_center_model.py`: Modelos (`ControlCenterSnapshot`, `MemorySummary`, `SkillSummary`, `VisionSummary`) e barramento (`ControlCenterBus`).
- `src/jarvis/application/control_center_coordinator.py`: Coordenador que agrega e publica dados.
- `src/jarvis/ui/control_center_model.py`: Extensões do Qt para abas do Control Center.
- Testes: `tests/ui/test_control_center_model.py`, `tests/application/test_control_center_coordinator.py`.

**Privacidade e segurança:**

- Snapshots contêm apenas dados agregados e metadados não sensíveis (contagens, IDs, categorias, sensibilidades).
- Conteúdo de memórias truncado a 60 chars na UI; Skills mostram apenas nome, triggers, contadores.
- Nenhum pixel, screenshot, argumento de Tool, caminho ou conteúdo sensível exposto.
- `ControlCenterBus` não replicado via IPC; permanece local.

**Testes adicionados:**

- `test_control_center_model.py`: Bus, snapshots, enums, summaries.
- `test_control_center_coordinator.py`: Refresh de memória, skills, visão, integração com telemetria, close.

**Validação:**

```text
836 passed, 1 skipped
compileall OK
git diff --check OK
```

## 18. Vision Semantic Perception Foundation (concluído)

A fundação de percepção semântica read-only foi implementada, expandindo a Vision Foundation existente.

**Novas capacidades:**

- **OCR (vision_extract_text)**: Extrai texto de uma região explícita da tela via Tesseract. Requer região (left, top, right, bottom) e idioma opcional (padrão `por+eng`). Retorna texto, confiança, região e idioma. Não persiste screenshot.
- **UI Tree (vision_get_ui_tree)**: Captura a árvore de acessibilidade da região ou tela inteira via Windows UI Automation (comtypes/uiautomation). Retorna hierarquia de elementos com nome, tipo de controle, retângulo delimitador, filhos e propriedades. Não persiste screenshot.

**Arquitetura:**

- `VisionBackend` (protocolo) estendido com `extract_text(region, language)` e `get_ui_tree(region)`.
- `PillowVisionBackend`: implementa OCR via `pytesseract` (opcional, falha fechada se indisponível).
- `WindowsUIAutomationBackend`: implementa UI tree via UI Automation (apenas Windows).
- `CompositeVisionBackend`: compõe ambos, usado pelo runtime.
- `VisionService`: expõe `extract_text` e `get_ui_tree` com capability-gating (`Capability.VISION`), validação de região, limite de pixels e privacidade (sem pixels/digests em respostas, audit ou IPC).

**Tools registradas:**

- `vision_extract_text` (MEDIUM): OCR read-only com região explícita.
- `vision_get_ui_tree` (MEDIUM): UI tree read-only com região opcional.

**Privacidade e segurança:**

- Ambas Tools são capability-gated (`VISION`), validadas por região e limite de pixels.
- Retornam apenas dados semânticos (texto/confiança ou árvore de elementos), nunca pixels, screenshots ou hashes.
- Não concedem autorização, não escolhem coordenadas de ação, não interpretam causalidade.
- Integração com `VisionToolExecutionVerifier` mantida para verificação visual pós-execução de Tools Desktop.

**Testes adicionados:**

- `test_extract_text_*` e `test_get_ui_tree_*` em `tests/vision/test_vision_service.py`: capability-gating, validação de região, limite de pixels, privacidade de resposta.
- `test_extract_text_tool_*` e `test_ui_tree_tool_*` em `tests/tools/test_vision.py`: execução, validação, auditoria, bloqueio quando desabilitado.

**Dependências opcionais:**

- `vision` extra em `pyproject.toml` agora inclui `pytesseract>=0.3,<1` para OCR.
- Windows UI Automation requer `comtypes` e `uiautomation` (instalados no ambiente Windows; backend falha fechada se indisponíveis).

## 19. Browser Agent Foundation (concluído)

A fundação do Browser Agent foi implementada, permitindo automação segura de navegador via Playwright com capacidade dedicada.

**Novas capacidades:**

- **Navegação (browser_navigate)**: Navega para URLs permitidas com validação de domínio.
- **Gestão de abas**: `browser_list_tabs`, `browser_new_tab`, `browser_close_tab`, `browser_switch_tab`.
- **Interação (browser_click, browser_type, browser_fill)**: Clique, digitação e preenchimento de formulários via seletores CSS.
- **Extração (browser_extract_text, browser_extract_multiple)**: Extração de texto e atributos de elementos.
- **Utilitários**: `browser_wait_for_selector`, `browser_get_snapshot`, `browser_go_back`, `browser_go_forward`, `browser_reload`, `browser_press_key`.

**Arquitetura:**

- `BrowserBackend` (protocolo) com implementação `PlaywrightBackend` (Chromium/Firefox/WebKit).
- `BrowserService`: Camada de domínio com validação de domínios permitidos/bloqueados, limite de abas, timeout de navegação, allowlist de teclas e scripts.
- `BrowserService.list_tabs_sync()`: Método síncrono para preflight.
- Tools registradas: 12 tools (SAFE/LOW/MEDIUM risk) capability-gated por `Capability.BROWSER`.
- Tools usam `_PreparedBrowserTool` base para validação de aba e seletor.

**Privacidade e segurança:**

- Capability `BROWSER` separada, desabilitada por padrão.
- Domínios permitidos/bloqueados configuráveis via `JARVIS_BROWSER_ALLOWED_DOMAINS` / `JARVIS_BROWSER_BLOCKED_DOMAINS`.
- Limite de abas (`browser_max_tabs`, padrão 10).
- Allowlist de teclas permitidas (Enter, Escape, Tab, setas, F1-F12, etc.).
- Allowlist de scripts JavaScript (querySelector, getElementById, title, location, innerWidth/Height).
- Seletores CSS obrigatórios para clique/digitação/extração; sem coordenadas absolutas.
- Não persiste screenshots por padrão; extração retorna apenas dados semânticos.

**Dependências opcionais:**

- `browser` extra em `pyproject.toml` inclui `playwright>=1.40,<2`.
- Requer instalação do Playwright: `playwright install`.

**Configuração:**

- `/config browser on|off` - habilita/desabilita capability.
- `/config browser allowed_domains <domínios>` - lista de domínios permitidos (CSV).
- `/config browser blocked_domains <domínios>` - lista de domínios bloqueados (CSV).
- `/config browser max_tabs <n>` - limite de abas simultâneas.

**Testes:**

- Testes de integração para navegação, interação, extração e gestão de abas (a serem adicionados).

## Remote Gateway Hardening (concluído)

O Gateway remoto voltou a possuir runtime TCP local funcional e uma fronteira explícita com o Core:

```text
Remote Client
    ↓
RemoteGateway
    ↓
RemoteCorePort
    ↓
Harness / Core
    ↓
Policy / Safety / ToolExecutor / Tool / Verification / Audit
```

- `RemoteCorePort` é a única interface operacional visível ao Gateway; não expõe `Harness`, `ToolExecutor`, `ToolRegistry`, grants ou execução direta de Tools.
- Chat remoto entra pelo mesmo `Harness.processar_mensagem()` usado pelo Core.
- Confirmações remotas retornam ao Core com o `session_id` e `challenge_id` originais. O Remote não cria `ConfirmationSession`, não chama `decide()` e não fabrica grant.
- Lifecycle de Gateway, bridge e transporte é explícito e idempotente por `start()` / `stop()`.
- O contrato `TransportServer` / `TransportConnection` opera com mensagens lógicas e estados explícitos de recepção; somente TCP/NDJSON foi implementado neste sprint.
- O protocolo usa discriminadores tipados imutáveis, schemas fechados, validação de tipos, campos obrigatórios, limites e rejeição de campos inesperados.
- O framing limita o buffer antes de newline a 64 KiB e encerra frames excedentes.
- Códigos de pairing são gerados apenas pelo comando local `--remote-pair`, persistidos temporariamente somente como hash, vinculados ao nome do dispositivo, one-time e com expiração.
- `DeviceRegistry` usa substituição atômica, falha fechada em corrupção e preserva revogações como tombstones duráveis. Revogação feita por comando local é recarregada pelo runtime e encerra sessões ativas.
- Tokens ficam armazenados somente como SHA-256; o token em claro é entregue apenas no pairing inicial e não é reenviado no reconnect.
- Eventos remotos usam a assinatura existente de `AuditLogger.log(tool_name, target, authorized, status)` sem conteúdo de chat, token ou argumentos de Tool.
- Estado remoto não inclui `snapshot.detail`; telemetria continua agregada.
- WebSocket, React e Web Control Center permaneceram fora deste sprint.

Testes em `tests/remote/` cobrem protocolo, framing incremental e excedente, transporte TCP, auth/pairing, persistência, corrupção, revogação, sessões, rate limiting, bridge, `RemoteCorePort`, confirmação original, reconnect, Kill Switch preservado por resposta do Core e invariantes arquiteturais.

Validação em 29/08/2026:

```text
tests/remote: 51 passed
total: 919 passed, 1 skipped
compileall OK
git diff --check OK
TCP loopback smoke test OK
```

## WebSocket Transport Foundation (concluído)

O Gateway remoto agora aceita WebSocket como transporte alternativo sem criar um segundo protocolo ou caminho operacional:

```text
Remote Protocol tipado
    ↓
TransportServer / TransportConnection
    ├── TCP / NDJSON
    └── WebSocket
            ↓
       RemoteGateway
            ↓
       RemoteCorePort
```

- `WebSocketTransportServer` e `WebSocketTransportConnection` adaptam frames para os mesmos `BaseMessage`, `ReceiveResult` e `ReceiveStatus` usados pelo TCP.
- Pairing, autenticação por token, sessões, permissões, rate limiting, chat, ping lógico, confirmação, revogação, audit e cleanup continuam implementados uma única vez no `RemoteGateway` e no `RemoteGatewayBridge`.
- `RemoteCorePort` permanece como única fronteira operacional com o Core. O transporte WebSocket não referencia `Harness`, Tools, `ToolExecutor`, `ToolRegistry` ou grants.
- O limite lógico permanece em 64 KiB. `websockets` aplica `max_size` durante a recepção; payload excedente encerra a conexão com código 1009 antes de chegar ao protocolo. Mensagens malformadas seguem o mesmo estado `INVALID` e fechamento fail-closed do TCP.
- Compressão WebSocket e ping automático ficam desabilitados. O ping lógico tipado existente permanece como mecanismo do protocolo remoto.
- Por padrão, somente clientes sem header `Origin` são aceitos. Navegadores exigem allowlist exata configurada por uma ou mais ocorrências de `--remote-websocket-origin`; páginas com origem não listada são recusadas no handshake.
- TLS reutiliza `--remote-cert` e `--remote-key`, produzindo WSS. Exposição fora de loopback continua exigindo `--remote-allow-lan`.
- Seleção de runtime: `--remote-transport tcp|websocket`; TCP continua sendo o padrão.
- Lifecycle é reiniciável e idempotente. Stop, desconexão, frame inválido, oversize e revogação removem a sessão e os senders associados; desconexão durante confirmação nega a sessão original através do Core.
- Reconnect usa o mesmo `device_id` e token inicial; o token em claro não é reenviado.
- React e Web Control Center continuam fora deste sprint.

Testes WebSocket usam sockets loopback reais e cobrem lifecycle/restart, Origin, pairing submission, autenticação, chat, ping, confirmação com IDs originais, reconnect, revogação externa, malformed, oversize, cleanup, Kill Switch preservado por resposta do Core e equivalência observável TCP/WebSocket.

Validação em 29/08/2026:

```text
tests/remote: 68 passed
total: 936 passed, 1 skipped
compileall OK
git diff --check OK
TCP/WebSocket loopback real OK
```

## Web Control Center Foundation (concluído)

Foi criada uma aplicação independente em `web-control-center/`, separada da Ambient UI:

```text
Ambient UI         = presença rápida e local
Web Control Center = conversa e observabilidade

Web Control Center
        ↓ WebSocket
Remote Gateway
        ↓
RemoteCorePort
        ↓
JARVIS Core
```

- Stack: React, TypeScript e Vite, sem backend adicional.
- Navegação inicial: `Chat`, `Overview`, `Activity`, `Security` e `Settings`.
- O frontend conecta diretamente ao transporte WebSocket existente e usa o mesmo protocolo lógico tipado, autenticação, sessões, rate limiting, confirmação e Core do cliente TCP.
- Chat usa somente `chat.message` / `chat.response`; não existe LLM ou agente no frontend. O histórico permanece apenas na memória da sessão da página.
- Estado e telemetria reutilizam `state.subscribe`, `telemetry.subscribe`, `state.update` e `telemetry.update` com descarte de sequências regressivas.
- Activity é derivada somente de eventos permitidos e não contém texto do chat, target de confirmação, argumentos, paths, memórias, tokens, handles, PIDs ou screenshots.
- Confirmação crítica exibe o pedido do Core e devolve exatamente os `session_id` e `challenge_id` recebidos. O frontend não cria, reinterpreta ou concede confirmação.
- Reconnect usa backoff até 30 segundos e para após oito tentativas. Desconexão manual, erro de protocolo e revogação interrompem reconnect; revogação também remove a credencial local.
- Pairing code é informado pelo usuário, nunca criado remotamente e apagado do formulário imediatamente após envio.
- Token não entra em React state, Web Storage, logs ou mensagens visíveis. Na V1 ele é cifrado com AES-GCM por chave Web Crypto não extraível persistida no IndexedDB. Isso melhora proteção em repouso, mas não protege contra comprometimento da própria origem/XSS; Origin exata, dependências e conteúdo servido continuam sendo fronteiras de segurança.
- A opção “Forget this browser” remove somente a cópia local. Revogação real continua sendo comando local no host JARVIS.
- Mensagens recebidas usam schema fechado no TypeScript e limite UTF-8 de 64 KiB. Capabilities devem corresponder à allowlist fixa do protocolo.
- O protocolo ganhou somente a extensão read-only `security.subscribe` / `security.update`. `RemoteCorePort.current_security()` retorna booleano agregado do Kill Switch e oito capabilities booleanas; motivo do Kill Switch, reset, Policy, Safety, grants e objetos internos não são expostos.
- Kill Switch permanece no Core e não possui controle remoto. Capabilities são somente leitura e não podem ser alteradas pela UI.
- O frontend não referencia nem acessa `Harness`, `ToolExecutor`, `ToolRegistry`, `SafetyManager`, grants, filesystem, processos ou backend Desktop.
- O Vite local exige que a origem exata, por exemplo `http://127.0.0.1:5173`, seja permitida no Gateway por `--remote-websocket-origin`.
- Google Workspace, Vision streaming, screenshots, Desktop direto, editor de Memory, Browser Agent UI, mobile, Internet pública e backup permaneceram fora do sprint.

Validação em 29/08/2026:

```text
frontend: 15 passed, 1 live test skipped por padrão
frontend live smoke: 1 passed
npm run build: OK
tests/remote: 75 passed
total Python: 943 passed, 1 skipped
compileall OK
git diff --check OK
Control Center TypeScript → WebSocket → Gateway → RemoteCorePort → Core fake OK
```

## Startup & Background Runtime Foundation (concluído)

O JARVIS possui agora um runtime composto explícito para execução interativa ou sem console permanente na sessão do usuário Windows:

```text
RuntimeLifecycle
    ├── Core / Harness único
    ├── Voice opcional
    ├── Remote Gateway opcional
    ├── Ambient UI opcional
    ├── health local
    └── cleanup reverso
```

- `--background` apenas desacopla o processo; não habilita Voice, Remote ou UI. No Windows, o processo filho oficial usa `pythonw.exe`, `CREATE_NO_WINDOW` e a mesma instalação Python.
- As combinações `Core`, `Core + Voice`, `Core + Remote`, `Core + UI` e combinações entre elas permanecem independentes. `Remote + Voice` compartilha o mesmo Harness, ToolExecutor, ConfirmationSession e serviços; não existe segundo Core.
- Turnos Voice e Remote são serializados no Harness para proteger memória conversacional, PendingAction, confirmações, trace e telemetria compartilhados.
- O lifecycle observável segue `STARTING → RUNNING → STOPPING → STOPPED`. Falha parcial executa rollback em ordem reversa; falha de health/logging não concede autorização nem impede cleanup.
- Shutdown para primeiro novas conexões Remote, drena trabalhos em andamento, solicita parada cooperativa do Voice, fecha Harness/ConfirmationSession, IPC/UI, Kill Switch listener e demais recursos. Operações em andamento não são terminadas à força.
- Single-instance usa mutex Win32 `Local\\JARVIS.Runtime` como autoridade. `data/runtime/instance.json` é somente metadata local; após crash, o kernel libera o mutex e a nova instância substitui metadata stale. Em outras plataformas, o fallback verifica PID antes de recuperar lock stale.
- Health local fica em `data/runtime/runtime-status.json` e inclui estado, PID local, uptime, capacidades iniciadas e erro agregado seguro de startup. Não é publicado automaticamente pelo Remote.
- `--runtime-status` consulta health local e `--runtime-stop` grava uma solicitação cooperativa vinculada ao `instance_id` atual.
- Logging técnico usa `logs/runtime.log`, rotação por tamanho (1 MB, três backups) e redação de tokens, Authorization Bearer e pairing codes inclusive em tracebacks. Não registra prompts, chat, memórias ou argumentos de Tools. O Audit permanece separado e autoritativo para ações operacionais.
- Autostart explícito usa `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`, adequado à sessão interativa necessária por Voice, UI, Desktop e Vision. Comandos: `--startup install|remove|status`; capacidades podem ser preservadas com `--voice`, `--remote` e `--ui` (ou aliases `--startup-*`). O comando armazenado não contém tokens ou credenciais.
- Windows Service não é a arquitetura adotada nesta fundação porque isolaria capacidades interativas da sessão do usuário.

Validação real no Windows em 29/08/2026:

- Core-only iniciou por `python -m jarvis --background`, sem console permanente, e health retornou PID/uptime/capacidade `core`;
- Remote-only iniciou com `--background --remote --remote-port 0`, sem habilitar Voice ou UI;
- uma segunda instância foi recusada sem encerrar a existente;
- `--runtime-stop` encerrou Core-only e Remote-only cooperativamente e o status voltou a inativo;
- autostart completou `install → status → remove → status` no registro do usuário atual;
- `966 passed, 1 skipped`; `compileall` e `git diff --check` passaram.

Limitações deliberadas:

- não há supervisor nem restart automático neste sprint;
- health e stop são locais e baseados em arquivo, ainda sem exposição no Web Control Center;
- falha do processo da Ambient UI depois do startup não reinicia a UI automaticamente;
- aceite live completo de Voice + UI + Remote com microfone/TTS/Ollama continua sendo validação manual separada; a fundação automatizada usa fakes nas fronteiras pesadas.

## 20. Google Workspace Integration

### Google Integration Foundation (concluído)

A fundação de autenticação Google Workspace foi implementada sem adicionar Tools ou autoridade operacional:

```text
CLI local
    ↓
GoogleAuthManager
    ├── OAuthFlowClient (google-auth-oauthlib)
    ├── LoopbackOAuthReceiver (127.0.0.1)
    ├── ProtectedFileCredentialStore
    │       ↓
    │   Windows DPAPI / usuário atual
    └── CredentialHandle interno
```

- `src/jarvis/integrations/google/` contém contratos, modelos agregados, erros tipados, OAuth adapter, callback loopback, storage, scopes, operação interprocesso e factory. Não referencia Harness, ToolExecutor, ToolRegistry ou LLM.
- O fluxo usa client OAuth do tipo Desktop app, navegador do sistema, callback HTTP em `127.0.0.1` com porta efêmera, PKCE S256, `state` aleatório validado com comparação constante, timeout e cancelamento. Callback com state incorreto é ignorado até callback válido ou expiração.
- O listener aceita somente `/oauth2/callback`, limita o request e aplica timeout também ao socket aceito para impedir bloqueio por request local incompleto. Nenhum authorization code é ecoado na página de sucesso.
- A compatibilidade oficial do SDK preserva o redirect HTTP loopback, mas entrega ao parser oauthlib uma cópia HTTPS da URL recebida, como `InstalledAppFlow.run_local_server`; a troca real de token continua em endpoint Google HTTPS.
- O fluxo básico `--google` continua solicitando somente `openid`. Scopes operacionais são solicitados separadamente pelas integrações que realmente os utilizam.
- Autenticação básica isolada não concede capability nem registra Tool. Estar conectado não concede Policy, Safety, risco, confirmação ou execução.
- O storage de produção usa DPAPI do usuário Windows com UI proibida e blob atômico em `data/google/credentials.bin`. Access token não é persistido. Refresh token, client metadata necessária e scopes ficam somente dentro do blob DPAPI; modelos sensíveis omitem esses campos de `repr`.
- Connect/status/disconnect são serializados entre processos pelo mutex Win32 `Local\\JARVIS.GoogleOAuth`. Crash libera o mutex pelo kernel.
- Refresh é automático no restore. `invalid_grant` passa para `revoked`; falha de rede passa para `error`; corrupção de storage falha fechada. Scope ausente nunca retorna `connected`.
- Disconnect marca primeiro a credencial como revogação pendente e não utilizável, tenta revogação remota e remove o blob após confirmação. Se a rede falhar, mantém somente o blob DPAPI em quarentena para retry; nova conexão não pode sobrescrevê-lo.
- CLI local: `--google connect --google-client-config <arquivo>`, `--google status` e `--google disconnect`. O caminho também pode vir de `JARVIS_GOOGLE_CLIENT_CONFIG`; tokens, secrets e authorization code nunca são argumentos.
- Dependências ficam no extra opcional `google`: `google-auth` e `google-auth-oauthlib`. Imports são lazy e não afetam startup sem integração Google.
- Logs técnicos redigem access/refresh/auth token, client secret e authorization code. A fundação não envia credenciais ao Audit, telemetry, Control Center, prompt, trace ou IPC.

Validação automatizada em 29/08/2026:

```text
1004 passed, 1 skipped
compileall OK
git diff --check OK
```

Os testes cobrem consentimento, state inválido, negação, timeout, cancelamento, callback loopback real, cliente lento, PKCE S256 no SDK oficial, refresh, revogação, network failure, scopes, corrupção, DPAPI real, operação interprocesso, idempotência, quarentena e ausência de secrets em logs/Audit/CLI.

Aceite real no Windows em 29/08/2026:

- consentimento explícito no navegador concluiu com `Google conectado`;
- um novo processo executou `--google status`, restaurou o material DPAPI, renovou o access token e retornou conectado;
- `--google disconnect` confirmou revogação e removeu a credencial local;
- status final retornou desconectado e `data/google/credentials.bin` não existia;
- nenhum token, authorization code, client secret ou identidade da conta foi exibido ou registrado.

Limitações deliberadas:

- ainda não há Drive, Docs, Sheets ou Gmail funcional;
- não existe identidade de conta exposta, Control Center Google ou gestão remota;
- o storage seguro de produção desta fase é Windows/DPAPI; outros sistemas falham fechados;
- erro de revogação remota exige retry de `disconnect` ou revisão manual das permissões da conta Google.

### Google Calendar Read-only Foundation (consolidado)

A primeira integração funcional do Google Workspace foi adicionada sem autoridade de escrita:

```text
Intent / Fast Path / LLM
    ↓
Calendar Tool
    ↓
ToolExecutor / Capability.GOOGLE_CALENDAR_READ / Safety / Audit
    ↓
CalendarProvider
    ↓
GoogleCalendarApiClient
    ↓
Google Calendar API
```

- Tools: `google_calendar_list_events`, `google_calendar_get_event` e `google_calendar_check_availability`, todas `LOW`, read-only e desabilitadas por padrão.
- Configuração local: `/config google_calendar on|off` ou `JARVIS_GOOGLE_CALENDAR`.
- OAuth Calendar é explícito e separado: `--google-calendar connect|status|disconnect`, com `openid + calendar.readonly`. Um upgrade vindo de credencial básica `openid` preserva a credencial anterior se o novo consentimento não for concluído; material em revogação pendente continua fail-closed.
- Credenciais são obtidas somente no momento da chamada. Refresh após HTTP 401 volta ao `GoogleAuthManager`, sob lock interprocesso, e persiste eventual rotação de material protegido.
- O provider normaliza eventos com horário e de dia inteiro, pagina resultados, rejeita token de paginação repetido, diferencia 404, 403, 429, rede e resposta malformada e limita o corpo HTTP recebido.
- Timezone é obtido do calendário consultado. Períodos usam relógio injetável e limites por datas locais; horários inexistentes ou ambíguos durante transições DST falham em vez de consultar outro instante.
- Disponibilidade usa o endpoint `freeBusy`, que não retorna títulos ou detalhes dos eventos, evitando a falsa alegação de agenda livre após paginação parcial.
- Fast Paths determinísticos cobrem hoje, amanhã, próximos sete dias, dia da semana, próxima semana e disponibilidade com dia e intervalo explícitos. Consultas ambíguas sem dia não entram no Fast Path.
- Audit usa somente alvos constantes (`google_calendar:events`, `google_calendar:event`, `google_calendar:availability`). IDs, calendário, período, horários e conteúdo não entram no alvo, trace de Skills ou debug de argumentos.
- Respostas de detalhe são minimizadas para título, horário e localização. Participantes, descrição, links, tokens e IDs não são incluídos.
- Calendar não inicia rede no startup. A capability pode ser registrada sem credencial; uma consulta sem consentimento falha fechada no caminho normal da Tool.
- O Corrective Checkpoint declarou `tzdata` no extra Google para instalações Windows sem base IANA do sistema, tornou falhas Calendar terminais também no Agent Loop e adicionou grounding defensivo para claims de agenda, eventos e disponibilidade sem Tool bem-sucedida.
- Paginação possui teto absoluto de 100 páginas, além da rejeição de token repetido. HTTP 403 com reason `rateLimitExceeded` ou `userRateLimitExceeded` é classificado como rate limit sem incluir o corpo da resposta no erro.
- O manager Calendar read-only aceita somente `openid + calendar.readonly`; credenciais com scopes adicionais falham fechadas. Uma futura Calendar Write Foundation deverá usar autoridade e storage explicitamente separados, e nunca poderá elevar a autoridade destas Tools read-only.

Validação automatizada em 29/08/2026:

```text
1035 passed, 1 skipped
compileall OK
git diff --check OK
```

Os testes cobrem scope upgrade, consentimento separado, rejeição de scopes adicionais, capability, startup sem credencial, dependência IANA no Windows, paginação limitada e replay de token, URL encoding, 404 versus rede, 403 rate limit/permission e 429, resposta malformada, timezone, all-day, DST gap/fold, `freeBusy`, falha terminal no Agent Loop, grounding Calendar, privacidade de Audit/trace, argumentos e Fast Paths com `llm_calls=0`.

Aceite real no Windows em 29/08/2026:

- consentimento de testador concluiu com `Google Calendar conectado` usando somente os scopes declarados;
- status e consultas em novos processos restauraram a credencial DPAPI;
- timezone do calendário e consultas de hoje, amanhã e próximos sete dias responderam pela API real;
- `freeBusy` real respondeu sem conteúdo de eventos;
- o caminho completo Harness → ToolExecutor → capability → provider → API executou lista e disponibilidade com `llm_calls=0`, `tool_calls=1` no último turno e alvos constantes no Audit;
- nenhum token, authorization code, client secret, identidade da conta, título, ID ou horário foi impresso durante o aceite.

Limitações deliberadas:

- não há criação, edição, cancelamento ou exclusão de eventos;
- não há descoberta/listagem de calendários, Control Center Calendar ou sincronização proativa;
- disponibilidade natural exige dia e horários explícitos; intervalos vagos permanecem no fluxo conversacional;
- conteúdo Calendar solicitado pelo usuário existe apenas na resposta operacional e não é persistido automaticamente em memória ou Skill.

### Google Calendar Write Foundation (concluído)

A autoridade de escrita foi adicionada como capability separada, sem transformar o provider read-only.

```text
Intent → Write Tool → ToolExecutor → Capability/Policy/Safety → CalendarWriteProvider → Google Calendar API
    ↓ pós-verificação via read-only provider
Audit → resposta
```

- Nova capability: `GOOGLE_CALENDAR_WRITE` (desabilitada por padrão).
- Novo scope: `https://www.googleapis.com/auth/calendar.events` (somente `openid + calendar.events`).
- Credencial e storage separados: `data/google/credentials_write.bin` via DPAPI.
- Manager write aceita somente `CALENDAR_WRITE_SCOPES`; scopes adicionais falham fechados.
- Tools criadas (todas `MEDIUM`, capability-gated):
  - `google_calendar_create_event`: cria evento com `summary`, `start`, `end`, `is_all_day`, `timezone`, `location`, `description`, `reminders` (popup/email).
  - `google_calendar_update_event`: atualiza evento existente por ID com os mesmos campos.
- Modelo de entrada tipado (`CalendarEventInput`): sem payload Google arbitrário; campos não suportados falham explicitamente.
- Resolução determinística de datas/timezone: ISO 8601 com timezone obrigatório; DST e horários inexistentes/ambíguos validados antes da execução.
- Preflight valida: credencial write válida, timestamps, timezone, campos obrigatórios, operação determinada.
- Pós-verificação obrigatória (read-after-write): após create/update, consulta o evento pelo ID retornado e compara campos essenciais (título). Só então afirma sucesso.
- Verificação de conflitos como informação, não bloqueio automático.
- Idempotência: retry de operação incerta primeiro determina se a escrita anterior ocorreu; estado desconhecido nunca vira sucesso.
- Update usa identidade do evento; alteração concorrente detectada falha em vez de sobrescrever cegamente (quando suportado pela API).
- Audit com alvo constante `google_calendar:event`; sem título, descrição, localização, participantes, tokens em trace/telemetry/Remote.
- Grounding expandido: bloqueia claims como “marquei”, “criei”, “agendei”, “mudei”, “alterei”, “cancelei”, “adicionei evento” sem Tool + verificação real.
- Fast Path determinístico não implementado para escrita (requer validação explícita); pedidos naturais usam Agent Loop com Tools restritas.
- Delete/cancel, convidados, recorrência avançada, notificações em massa: fora deste sprint.
- Configuração: `/config google_calendar_write on|off` ou `JARVIS_GOOGLE_CALENDAR_WRITE`.
- OAuth write: `--google-calendar-write connect|status|disconnect` (a implementar no CLI se necessário).

Validação automatizada em 30/08/2026:

```text
1036 passed, 1 skipped
compileall OK
git diff --check OK
```

Testes cobrem: scopes write isolados, read/write capability isolation, modelos tipados, validação de entrada, timezone/DST, preflight, pós-verificação, idempotência, concorrência, grounding write, Audit sem conteúdo sensível, read-only provider incapaz de escrever.

Limitações deliberadas da V1:

- delete/cancel não implementado;
- convidados/participantes e envio de convites fora;
- recorrência (edição de série vs ocorrência única) não suportada;
- descoberta de calendários não implementada;
- Fast Path para escrita não implementado;
- scheduling proativo (conflito → sugestão automática) fora.

## 21. Regras para continuidade

A fundação da interface de controle (Control Center) foi implementada, expandindo a Ambient HUD existente com um painel de abas dedicado à visualização agregada de telemetria, memória, skills e visão.

**Novas capacidades:**

- **Aba Telemetria**: Exibe métricas agregadas do último turno (TURN, LLM, TOOLS) e saúde do audit (AUDIT OK/ERROR), substituindo os indicadores decorativos anteriores por dados reais.
- **Aba Memória**: Lista memórias persistentes com sensibilidade (public/private/sensitive), consentimento (SIM/NÃO), retenção e ID. Atualiza automaticamente quando memórias são adicionadas/removidas.
- **Abas Skills**: Mostra skills aprendidas com contadores de execução/sucesso, triggers e IDs.
- **Aba Visão**: Status da capacidade Vision (habilitada/desabilitada) e última observação visual.

**Arquitetura:**

- `ControlCenterBus`: Barramento thread-safe para snapshots agregados (`ControlCenterSnapshot`) contendo telemetria, memória, skills e visão.
- `ControlCenterCoordinator`: Agrega dados de `MemoryStore`, `SkillStore`, `VisionService` e `TelemetryBus`, publicando no bus periodicamente e sob demanda.
- `AmbientWindow` (Qt): Painel expandido com `QTabWidget` contendo abas TELEMETRY, MEMORY, SKILLS, VISION. Assina `ControlCenterBus`, `AgentStateBus` e `TelemetryBus` via relays Qt.
- `TelemetryBus`: Continua separado do `AgentStateBus`; agora também alimenta o `ControlCenterBus` via `ControlCenterCoordinator`.
- IPC: `LocalStateServer` replica tanto `StateSnapshot` quanto `TelemetrySnapshot` (tipo `telemetry`); `ControlCenterSnapshot` não é replicado via IPC (apenas local).

**Novos arquivos:**

- `src/jarvis/ui/control_center_model.py`: Modelos (`ControlCenterSnapshot`, `MemorySummary`, `SkillSummary`, `VisionSummary`) e barramento (`ControlCenterBus`).
- `src/jarvis/application/control_center_coordinator.py`: Coordenador que agrega e publica dados.
- `src/jarvis/ui/control_center_model.py`: Extensões do Qt para abas do Control Center.
- Testes: `tests/ui/test_control_center_model.py`, `tests/application/test_control_center_coordinator.py`.

**Privacidade e segurança:**

- Snapshots contêm apenas dados agregados e metadados não sensíveis (contagens, IDs, categorias, sensibilidades).
- Conteúdo de memórias truncado a 60 chars na UI; Skills mostram apenas nome, triggers, contadores.
- Nenhum pixel, screenshot, argumento de Tool, caminho ou conteúdo sensível exposto.
- `ControlCenterBus` não replicado via IPC; permanece local.

**Testes adicionados:**

- `test_control_center_model.py`: Bus, snapshots, enums, summaries.
- `test_control_center_coordinator.py`: Refresh de memória, skills, visão, integração com telemetria, close.

**Validação:**

```text
836 passed, 1 skipped
compileall OK
git diff --check OK
```

## 18. Vision Semantic Perception Foundation (concluído)

1. fundação da interface de controle (Control Center) para status, telemetria, memórias, Skills e visão;
2. browser agent;
3. startup automático e background runtime;
4. acesso remoto seguro.

## 17. Vision Semantic Perception Foundation (concluído)

A fundação de percepção semântica read-only foi implementada, expandindo a Vision Foundation existente.

**Novas capacidades:**

- **OCR (vision_extract_text)**: Extrai texto de uma região explícita da tela via Tesseract. Requer região (left, top, right, bottom) e idioma opcional (padrão `por+eng`). Retorna texto, confiança, região e idioma. Não persiste screenshot.
- **UI Tree (vision_get_ui_tree)**: Captura a árvore de acessibilidade da região ou tela inteira via Windows UI Automation (comtypes/uiautomation). Retorna hierarquia de elementos com nome, tipo de controle, retângulo delimitador, filhos e propriedades. Não persiste screenshot.

**Arquitetura:**

- `VisionBackend` (protocolo) estendido com `extract_text(region, language)` e `get_ui_tree(region)`.
- `PillowVisionBackend`: implementa OCR via `pytesseract` (opcional, falha fechada se indisponível).
- `WindowsUIAutomationBackend`: implementa UI tree via UI Automation (apenas Windows).
- `CompositeVisionBackend`: compõe ambos, usado pelo runtime.
- `VisionService`: expõe `extract_text` e `get_ui_tree` com capability-gating (`Capability.VISION`), validação de região, limite de pixels e privacidade (sem pixels/digests em respostas, audit ou IPC).

**Tools registradas:**

- `vision_extract_text` (MEDIUM): OCR read-only com região explícita.
- `vision_get_ui_tree` (MEDIUM): UI tree read-only com região opcional.

**Privacidade e segurança:**

- Ambas Tools são capability-gated (`VISION`), validadas por região e limite de pixels.
- Retornam apenas dados semânticos (texto/confiança ou árvore de elementos), nunca pixels, screenshots ou hashes.
- Não concedem autorização, não escolhem coordenadas de ação, não interpretam causalidade.
- Integração com `VisionToolExecutionVerifier` mantida para verificação visual pós-execução de Tools Desktop.

**Testes adicionados:**

- `test_extract_text_*` e `test_get_ui_tree_*` em `tests/vision/test_vision_service.py`: capability-gating, validação de região, limite de pixels, privacidade de resposta.
- `test_extract_text_tool_*` e `test_ui_tree_tool_*` em `tests/tools/test_vision.py`: execução, validação, auditoria, bloqueio quando desabilitado.

**Dependências opcionais:**

- `vision` extra em `pyproject.toml` agora inclui `pytesseract>=0.3,<1` para OCR.
- Windows UI Automation requer `comtypes` e `uiautomation` (instalados no ambiente Windows; backend falha fechada se indisponíveis).

## 18. Google Workspace Integration

> Planejamento de integração ao ecossistema Google através de APIs oficiais. Não implementar neste sprint; registrado para continuidade do roadmap.

### Objetivo

Integrar o JARVIS ao ecossistema Google através das APIs oficiais, permitindo que o agente consulte e opere serviços pessoais do usuário de forma segura, auditável e compatível com a arquitetura de Tools.

A integração não deve depender de automação visual do navegador quando houver API oficial disponível.

### Arquitetura esperada

```text
User Intent
    ↓
JARVIS Core
    ↓
Tool
    ↓
Policy / Capability / Safety
    ↓
Google Integration Provider
    ↓
Google API
    ↓
Verification
    ↓
Audit
    ↓
Response
```

O LLM nunca recebe credenciais, refresh tokens ou acesso direto às APIs.

### Princípios

- OAuth 2.0 para autenticação.
- Nunca armazenar senha da conta Google.
- Nunca expor access token ou refresh token ao LLM.
- Nunca registrar tokens no Audit.
- Nunca versionar secrets ou arquivos de credenciais no Git.
- Preferir armazenamento seguro de credenciais no Windows.
- Aplicar mínimo privilégio através de scopes específicos.
- Solicitar novas permissões apenas quando uma nova integração realmente precisar delas.
- Todas as operações continuam passando por Capability, Policy, SafetyManager, ToolExecutor e Audit.
- A integração Google não cria um caminho alternativo para contornar a arquitetura de segurança.
- Operações devem verificar o resultado real antes de afirmar sucesso.

### Arquitetura planejada

```text
src/jarvis/integrations/google/
├── auth.py
├── client.py
├── models.py
├── calendar.py
├── drive.py
├── docs.py
└── sheets.py

src/jarvis/tools/
├── google_calendar.py
├── google_drive.py
├── google_docs.py
└── google_sheets.py
```

Evitar acoplamento direto entre Harness/Application Layer e SDKs do Google.

Quando possível, utilizar interfaces/providers que permitam futuramente outras implementações:

```text
CalendarProvider
├── GoogleCalendarProvider
└── FutureCalendarProvider
```

### Fase 1 — Google Authentication Foundation

Implementar:

- OAuth 2.0;
- fluxo inicial de consentimento;
- renovação segura de access tokens;
- armazenamento seguro do refresh token;
- scopes mínimos;
- revogação/desconexão da conta;
- estado de conexão consultável pelo JARVIS;
- tratamento explícito de token expirado/revogado;
- testes sem depender da conta Google real.

Não implementar todos os serviços Google nesta fase.

### Fase 2 — Google Calendar

Primeira integração funcional.

Capacidades planejadas:

- listar próximos eventos;
- consultar agenda por período;
- verificar disponibilidade;
- obter detalhes de evento;
- criar evento;
- editar evento;
- cancelar/excluir evento;
- configurar lembretes;
- trabalhar corretamente com timezone;
- interpretar referências como hoje, amanhã, sexta-feira e próxima semana antes de chamar a Tool.

Exemplos futuros:

> "O que eu tenho amanhã?"
>
> "Tenho alguma coisa sexta à tarde?"
>
> "Marca dentista sexta às 14h."
>
> "Muda minha reunião para 16h."
>
> "Quando eu tenho duas horas livres essa semana?"

A leitura da agenda não deve depender do LLM para operações determinísticas de data, timezone ou conflito.

### Fase 3 — Google Drive

Capacidades planejadas:

- localizar arquivos;
- listar arquivos recentes;
- obter metadados;
- identificar Docs, Sheets e outros tipos;
- abrir/referenciar arquivos por ID;
- servir como camada de descoberta para Docs e Sheets.

Não permitir acesso irrestrito a arquivos sem respeitar scopes e política.

### Fase 4 — Google Docs

Capacidades planejadas:

- criar documento;
- ler conteúdo;
- adicionar conteúdo;
- atualizar trechos suportados;
- gerar documentos a partir de conteúdo produzido pelo JARVIS.

Exemplos:

> "Cria um documento com esse resumo."
>
> "Adiciona isso nas minhas anotações do projeto."

### Fase 5 — Google Sheets

Capacidades planejadas:

- criar planilha;
- consultar ranges;
- adicionar linhas;
- atualizar células/ranges;
- estruturar dados;
- utilizar código para cálculos determinísticos antes de recorrer ao LLM.

Exemplos:

> "Adiciona esse gasto na minha planilha."
>
> "Quanto eu gastei este mês?"
>
> "Cria uma planilha para acompanhar os benchmarks do JARVIS."

### Fase 6 — Gmail

Integração futura e separada devido ao maior nível de sensibilidade.

Inicialmente priorizar:

- leitura/pesquisa;
- resumo;
- identificação de mensagens importantes.

Envio, resposta, exclusão e outras operações mutáveis devem receber análise de risco específica antes de serem liberadas.

### Integração com o Control Center

O futuro JARVIS Control Center deve poder exibir:

- próxima agenda;
- eventos do dia;
- status da conexão Google;
- calendários conectados;
- documentos recentes;
- planilhas recentes;
- atividades realizadas pelo JARVIS.

A interface nunca acessa diretamente as APIs Google.

```text
Control Center
    ↓
JARVIS Gateway
    ↓
Core
    ↓
Tools
    ↓
Google Provider
```

### Evolução futura

Google Calendar poderá ser combinado com:

- Personal Memory;
- rotina;
- Tasks;
- Automations;
- proatividade BALANCED;
- notificações;
- Control Center;
- acesso mobile/remoto.

Exemplo futuro:

> "Marca isso em um horário que não conflite com meus compromissos."

JARVIS poderá:

```text
Calendar
+
Memory / Preferences
+
Scheduling Logic
+
Policy
→ propor horário
→ receber decisão quando necessário
→ executar via Calendar Tool
→ verificar criação
→ registrar em Audit
```

### Ordem recomendada

```text
Google Integration Foundation
    ↓
OAuth / Secure Credential Storage
    ↓
Google Calendar
    ↓
Google Drive
    ↓
Google Docs
    ↓
Google Sheets
    ↓
Gmail
    ↓
Calendar + Memory + Tasks + Proactivity
```

E no roadmap principal:

```text
Vision Verification                  ✅
        ↓
Target-Bound Vision                  ✅
        ↓
Control Center Foundation
        ↓
Google Integration Foundation
        ↓
Google Calendar
        ↓
Desktop Input seguro
        ↓
Vision Understanding
        ↓
Personal Memory / Obsidian
        ↓
Google Drive / Docs / Sheets
        ↓
Scheduling + Tasks + Proactivity
        ↓
Gmail
        ↓
Gateway / Mobile / Remote
```

Calendar entra cedo porque já vai começar a transformar o JARVIS em um assistente pessoal de verdade; Drive, Docs e Sheets podem evoluir posteriormente conforme o Core amadurece.

## 19. Regras para continuidade

1. ampliar Vision para percepção semântica read-only (OCR e UI tree), sem persistir screenshots por padrão;
2. manter percepção incapaz de conceder autorização ou escolher coordenadas de ação;
3. browser agent;
4. startup automático e background runtime;
5. acesso remoto seguro.

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

## 18. Google Workspace Integration

> Planejamento de integração ao ecossistema Google através de APIs oficiais. Não implementar neste sprint; registrado para continuidade do roadmap.

### Objetivo

Integrar o JARVIS ao ecossistema Google através das APIs oficiais, permitindo que o agente consulte e opere serviços pessoais do usuário de forma segura, auditável e compatível com a arquitetura de Tools.

A integração não deve depender de automação visual do navegador quando houver API oficial disponível.

### Arquitetura esperada

```text
User Intent
    ↓
JARVIS Core
    ↓
Tool
    ↓
Policy / Capability / Safety
    ↓
Google Integration Provider
    ↓
Google API
    ↓
Verification
    ↓
Audit
    ↓
Response
```

O LLM nunca recebe credenciais, refresh tokens ou acesso direto às APIs.

### Princípios

- OAuth 2.0 para autenticação.
- Nunca armazenar senha da conta Google.
- Nunca expor access token ou refresh token ao LLM.
- Nunca registrar tokens no Audit.
- Nunca versionar secrets ou arquivos de credenciais no Git.
- Preferir armazenamento seguro de credenciais no Windows.
- Aplicar mínimo privilégio através de scopes específicos.
- Solicitar novas permissões apenas quando uma nova integração realmente precisar delas.
- Todas as operações continuam passando por Capability, Policy, SafetyManager, ToolExecutor e Audit.
- A integração Google não cria um caminho alternativo para contornar a arquitetura de segurança.
- Operações devem verificar o resultado real antes de afirmar sucesso.

### Arquitetura planejada

```text
src/jarvis/integrations/google/
├── auth.py
├── client.py
├── models.py
├── calendar.py
├── drive.py
├── docs.py
└── sheets.py

src/jarvis/tools/
├── google_calendar.py
├── google_drive.py
├── google_docs.py
└── google_sheets.py
```

Evitar acoplamento direto entre Harness/Application Layer e SDKs do Google.

Quando possível, utilizar interfaces/providers que permitam futuramente outras implementações:

```text
CalendarProvider
├── GoogleCalendarProvider
└── FutureCalendarProvider
```

### Fase 1 — Google Authentication Foundation

Implementar:

- OAuth 2.0;
- fluxo inicial de consentimento;
- renovação segura de access tokens;
- armazenamento seguro do refresh token;
- scopes mínimos;
- revogação/desconexão da conta;
- estado de conexão consultável pelo JARVIS;
- tratamento explícito de token expirado/revogado;
- testes sem depender da conta Google real.

Não implementar todos os serviços Google nesta fase.

### Fase 2 — Google Calendar

Primeira integração funcional.

Capacidades planejadas:

- listar próximos eventos;
- consultar agenda por período;
- verificar disponibilidade;
- obter detalhes de evento;
- criar evento;
- editar evento;
- cancelar/excluir evento;
- configurar lembretes;
- trabalhar corretamente com timezone;
- interpretar referências como hoje, amanhã, sexta-feira e próxima semana antes de chamar a Tool.

Exemplos futuros:

> "O que eu tenho amanhã?"
>
> "Tenho alguma coisa sexta à tarde?"
>
> "Marca dentista sexta às 14h."
>
> "Muda minha reunião para 16h."
>
> "Quando eu tenho duas horas livres essa semana?"

A leitura da agenda não deve depender do LLM para operações determinísticas de data, timezone ou conflito.

### Fase 3 — Google Drive

Capacidades planejadas:

- localizar arquivos;
- listar arquivos recentes;
- obter metadados;
- identificar Docs, Sheets e outros tipos;
- abrir/referenciar arquivos por ID;
- servir como camada de descoberta para Docs e Sheets.

Não permitir acesso irrestrito a arquivos sem respeitar scopes e política.

### Fase 4 — Google Docs

Capacidades planejadas:

- criar documento;
- ler conteúdo;
- adicionar conteúdo;
- atualizar trechos suportados;
- gerar documentos a partir de conteúdo produzido pelo JARVIS.

Exemplos:

> "Cria um documento com esse resumo."
>
> "Adiciona isso nas minhas anotações do projeto."

### Fase 5 — Google Sheets

Capacidades planejadas:

- criar planilha;
- consultar ranges;
- adicionar linhas;
- atualizar células/ranges;
- estruturar dados;
- utilizar código para cálculos determinísticos antes de recorrer ao LLM.

Exemplos:

> "Adiciona esse gasto na minha planilha."
>
> "Quanto eu gastei este mês?"
>
> "Cria uma planilha para acompanhar os benchmarks do JARVIS."

### Fase 6 — Gmail

Integração futura e separada devido ao maior nível de sensibilidade.

Inicialmente priorizar:

- leitura/pesquisa;
- resumo;
- identificação de mensagens importantes.

Envio, resposta, exclusão e outras operações mutáveis devem receber análise de risco específica antes de serem liberadas.

### Integração com o Control Center

O futuro JARVIS Control Center deve poder exibir:

- próxima agenda;
- eventos do dia;
- status da conexão Google;
- calendários conectados;
- documentos recentes;
- planilhas recentes;
- atividades realizadas pelo JARVIS.

A interface nunca acessa diretamente as APIs Google.

```text
Control Center
    ↓
JARVIS Gateway
    ↓
Core
    ↓
Tools
    ↓
Google Provider
```

### Evolução futura

Google Calendar poderá ser combinado com:

- Personal Memory;
- rotina;
- Tasks;
- Automations;
- proatividade BALANCED;
- notificações;
- Control Center;
- acesso mobile/remoto.

Exemplo futuro:

> "Marca isso em um horário que não conflite com meus compromissos."

JARVIS poderá:

```text
Calendar
+
Memory / Preferences
+
Scheduling Logic
+
Policy
→ propor horário
→ receber decisão quando necessário
→ executar via Calendar Tool
→ verificar criação
→ registrar em Audit
```

### Ordem recomendada

```text
Google Integration Foundation
    ↓
OAuth / Secure Credential Storage
    ↓
Google Calendar
    ↓
Google Drive
    ↓
Google Docs
    ↓
Google Sheets
    ↓
Gmail
    ↓
Calendar + Memory + Tasks + Proactivity
```

No roadmap principal:

```text
Vision Verification                  ✅
        ↓
Target-Bound Vision
        ↓
Control Center Foundation
        ↓
Google Integration Foundation
        ↓
Google Calendar
        ↓
Desktop Input seguro
        ↓
Vision Understanding
        ↓
Personal Memory / Obsidian
        ↓
Google Drive / Docs / Sheets
        ↓
Scheduling + Tasks + Proactivity
        ↓
Gmail
        ↓
Gateway / Mobile / Remote
```

Calendar entra cedo porque já vai começar a transformar o JARVIS em um assistente pessoal de verdade; Drive, Docs e Sheets podem evoluir posteriormente conforme o Core amadurece.

## 19. Regras para continuidade

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
