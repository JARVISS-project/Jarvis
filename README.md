# Jarvis

Assistente pessoal inteligente desenvolvido em Python utilizando Ollama.

Projeto criado para estudar agentes de IA, automação, segurança e interação com o sistema.
## Voz neural e personalidade

O modo de voz pode usar o Windows SAPI como fallback ou o Chatterbox
Multilingual V3 local para fala neural em português. O backend é carregado de
forma preguiçosa: o modelo neural só é carregado quando a primeira fala é
gerada ou durante o setup explícito.

Instalação opcional da voz neural:

```powershell
python -m pip install -e ".[voice,neural-voice]"
python -m jarvis --setup-neural-voice
```

Com o extra instalado, o provider `auto` prefere Chatterbox e mantém SAPI como
fallback. Também é possível fixar o provider:

```text
/config voice provider chatterbox
/config voice
```

Para uma voz própria, use um clipe de referência que você tenha permissão para
usar. Um caminho dentro de `data/voices/` fica fora do Git pela configuração
atual do projeto:

```text
/config voice reference "C:\Users\...\Jarvis\data\voices\jarvis-reference.wav"
```

Para remover a referência:

```text
/config voice reference off
```

O tratamento padrão é inspirado no estilo de um assistente pessoal elegante:
`senhor` aparece apenas ocasionalmente, sem ser repetido em toda frase. O modo
pode ser alterado sem mexer no código:

```text
/config persona treatment senhor
/config persona treatment natural
/config persona treatment direto
```

As respostas de Tool são reformuladas pelo LLM antes de serem apresentadas. Ao
pedir para ler uma nota, Jarvis faz uma introdução curta e lê o conteúdo de modo
fiel; ao pedir resumo ou assunto, ele sintetiza; ao pedir leitura exata, preserva
o texto. Pedidos compostos devem ser concluídos na ordem antes da resposta
final, quando as Tools e políticas permitirem.
