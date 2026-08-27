# JARVIS — Estado Atual do Projeto

> Documento vivo de continuidade técnica. O Git continua sendo a fonte de verdade do código; este arquivo registra decisões, arquitetura e marcos conhecidos.

Última consolidação: **27/08/2026**

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

## 8. Obsidian

A integração é local e usa o Vault diretamente, com guard próprio. Tools cobrem list/search/read/open/create/append/delete note. Operações destrutivas continuam sujeitas a risco e confirmação.

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

## 13. Testes

Marco conhecido após o segundo sprint de decomposição do Harness:

```text
623 passed
compileall OK
```

Esse resultado no ambiente de análise usou stubs somente para permitir imports de dependências externas ausentes; não substitui teste real de voz, Ollama ou Qt no PC principal.

Sempre validar no PC principal:

```powershell
git status
python -m pytest
```

## 14. Arquivos de continuidade

- `AGENTS.md`: regras do repositório; deve existir na raiz.
- `docs/JARVIS_STATE.md`: documento vivo oficial de estado.
- patches, dumps e arquivos locais de recovery na raiz devem permanecer ignorados pelo Git.

## 15. Próximas prioridades

1. validar o V9 no PC principal e consolidar no Git quando autorizado;
2. melhorar confiabilidade conversacional, grounding e contexto;
3. evoluir memória pessoal persistente com autorização clara;
4. transformar indicadores decorativos da HUD em telemetria real;
5. iniciar Desktop Tools em módulo próprio, sem acoplar ao Harness;
6. visão e verificação pós-ação;
7. browser agent;
8. startup automático e background runtime;
9. acesso remoto seguro.

## 16. Regras para continuidade

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
