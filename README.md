# Jarvis

Assistente pessoal local em Python com Ollama, voz, Tools, segurança, contexto e
integração com Obsidian.

O projeto é organizado pela regra:

> **O LLM interpreta. O código autoriza. A Tool executa. A Skill lembra o procedimento.**

## Modo de voz

No `--voice`, o Jarvis aquece o STT e, opcionalmente, o LLM antes de anunciar a
wake word. Isso desloca o cold start para a inicialização e evita que a primeira
fala real pague o carregamento dos modelos.

```powershell
python -m jarvis --voice
```

O provider `auto` prefere a voz rápida Piper quando a voz pt-BR estiver
preparada; Chatterbox continua disponível como modo de maior qualidade e SAPI
permanece como fallback.

### Piper pt-BR rápido

```powershell
python -m pip install -e ".[voice,fast-voice]"
python -m jarvis --setup-fast-voice
```

Dentro do Jarvis:

```text
/config voice mode fast
/config voice pace fast
/config voice pace natural
/config voice pace calm
```

Para comparar ritmos sem alterar `data/settings.json`, use um override apenas
na execução atual. `natural` é o ponto de partida recomendado para o Razo:

```powershell
python -m jarvis --voice --ui --tts-pace natural
python -m jarvis --voice --ui --tts-pace calm
python -m jarvis --voice --ui --tts-pace fast
```

Os perfis priorizam uma cadência mais calma para a voz Razo (`fast=1.00`,
`natural=1.18`, `calm=1.30`). O perfil `natural` continua sendo o padrão; use
`fast` quando preferir respostas mais ágeis e `calm` quando quiser uma fala
deliberadamente mais pausada.

### Chatterbox neural

```powershell
python -m pip install -e ".[voice,neural-voice]"
python -m jarvis --setup-neural-voice
```

Para comparar os backends:

```text
/config voice mode fast
/config voice mode quality
/config voice mode auto
```

Uma referência de voz pode ser configurada somente com áudio que o usuário
tenha permissão para utilizar:

```text
/config voice reference "C:\caminho\voz.wav"
/config voice reference off
```

## Persona e resposta falada

Em voz, o Jarvis prioriza respostas curtas e naturais. Leituras literais,
explicações detalhadas ou pedidos explícitos de aprofundamento podem ser maiores.

```text
/config persona treatment senhor
/config persona treatment natural
/config persona treatment direto
```


## Presença por voz e Ambient HUD

A chamada cotidiana é **`Jarvis`**. O detector continua usando o modelo local
`hey_jarvis` do openWakeWord, que também pode reconhecer a forma curta, embora
ela possa exigir calibração de sensibilidade. O threshold pode ser testado sem
alterar código:

```powershell
python -m jarvis --voice --wake-word-threshold 0.45
```

Ao detectar a wake word, o Jarvis pode confirmar presença com frases curtas
rotativas como `Oi, tô aqui.`, `Diga.` e `Estou ouvindo.` sem chamar o LLM. O
microfone de comando só é aberto depois que essa fala termina, mantendo o modo
half-duplex que evita o Jarvis transcrever a própria voz.

A Ambient HUD pode acompanhar o Voice Runtime em um **processo separado**:

```powershell
python -m jarvis --voice --ui
```

O Core publica apenas snapshots de estado (`LISTENING`, `PROCESSING`,
`SPEAKING`, `CONFIRMATION` etc.) por um canal TCP somente em loopback. A UI não
possui canal de comandos de volta para o Core e detalhes contextuais não são
serializados nesse transporte. Fechar a HUD não encerra o Voice Runtime. Enquanto
o runtime continuar aberto, a interface pode ser iniciada novamente com:

```powershell
python -m jarvis.ui --live
```

O modo visual isolado continua disponível para desenvolvimento:

```powershell
python -m jarvis.ui
```

## Fast Paths, linguagem natural e grounding

Intenções determinísticas como data/hora podem ser resolvidas sem uma rodada do
LLM. Pedidos comuns do Obsidian aceitam variações de fala como `leia ela`,
`leia a Jarvis Tool`, `lista minhas anotações` e `dentro de X coloque Y e depois
leia`, continuando a executar cada Tool pelo executor/política normal. Inventário
de Skills por voz também é lido diretamente do `SkillStore`, sem o LLM inventar
capacidades que não estejam realmente registradas.

Quando um pedido parece exigir uma ação real e a primeira resposta do modelo não
contém Tool calls, o Harness permite uma segunda tentativa orientada em vez de
exigir que o usuário repita uma frase exata. Um guard de grounding rejeita
alegações como `criei`, `abri`, `listei` ou `encontrei` quando nenhuma Tool foi
executada com sucesso na interação atual.

## Skills — memória procedural

Skills registram **como** uma sequência de Tools já executada com sucesso pode
ser repetida. O arquivo executável local fica em `data/skills.json`.

O Markdown no Obsidian é apenas um espelho legível para consulta e Graph View;
ele nunca é lido de volta como autorização ou código executável.

Depois de uma interação que executou Tools com sucesso:

```text
/skill learn "Ler nota de teste"
/skills
/skill show "Ler nota de teste"
/skill run "Ler nota de teste"
/skill forget "Ler nota de teste"
/skill sync
```

Também é possível dizer de forma determinística:

```text
Jarvis, aprenda esse procedimento como Ler nota de teste
```

A Skill não armazena grants nem reduz risco. Ao executá-la novamente, cada Tool
passa pelo `ToolExecutor`, `CapabilityPolicy`, `SafetyManager` e confirmação
aplicável. Uma ação HIGH continua HIGH mesmo depois de aprendida.

Quando o Obsidian está configurado com escrita habilitada, o Jarvis pode gerar
um espelho em `Jarvis/Skills/`, criando nós úteis no Graph View.
