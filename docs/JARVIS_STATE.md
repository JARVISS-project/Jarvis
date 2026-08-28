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
- `desktop_open_application` — MEDIUM / `DESKTOP_CONTROL`.

Abertura de aplicativo usa catálogo explícito. O LLM não fornece executável, argumentos, shell ou PowerShell. Catálogo inicial: `notepad`, `calculator`, `explorer`. Depois do lançamento, o backend verifica dentro de um prazo limitado se um processo com identidade definida no catálogo está em execução; sem essa evidência, a Tool retorna erro. Essa pós-condição comprova o estado em execução depois da solicitação, sem alegar causalidade quando o aplicativo já estava aberto. Resolução de janela prefere título exato e só aceita match parcial quando é único; ambiguidades falham em vez de escolher arbitrariamente.

Pedidos explícitos de janela, como “foca a janela do Obsidian”, priorizam Desktop em vez da integração de notas. Depois do kill switch, as Tools de controle permanecem roteáveis e devolvem diretamente o bloqueio estruturado, sem depender de paráfrase do LLM.

Controle possui `DesktopKillSwitch` fora do LLM. No Windows, quando `desktop_control` está habilitado, o runtime registra **Ctrl+Alt+K** por Win32. Falha no registro aciona o kill switch e deixa o controle fail-closed. Uma vez acionado, o LLM não possui Tool para resetá-lo; o rearmamento normal é feito reiniciando o runtime.

As ações atuais (`focus/open`) são curtas. Qualquer sequência futura de mouse/teclado deverá consultar o kill switch entre passos; não será aceitável ter uma automação longa que só verifique cancelamento antes de começar.

Configuração local:

```text
/config desktop
/config desktop read on|off
/config desktop control on|off
```

Ainda não implementado neste estágio: mouse/teclado arbitrários, screenshots, Vision, automação de navegador ou execução de comandos.

## 14. Testes

Marco conhecido após a revisão de autonomia e hardening de Desktop Automation:

```text
687 passed, 1 skipped
compileall OK
```

Esse resultado no ambiente de análise usou stubs somente para permitir imports de dependências externas ausentes; não substitui teste real de voz, Ollama ou Qt no PC principal.

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

1. validar `desktop read/control` e o kill switch no Windows real;
2. adicionar contexto/referência natural de janelas sem reduzir ambiguidade segura;
3. só então introduzir mouse/teclado com limites, cancelamento e verificação pós-ação;
4. evoluir memória pessoal persistente com autorização clara;
5. transformar indicadores decorativos da HUD em telemetria real;
6. Vision para percepção e verificação da tela;
7. browser agent;
8. startup automático e background runtime;
9. acesso remoto seguro.

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
