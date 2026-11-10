# Instalar e configurar para você

## Instalação

Baixe o `.exe` na [página de versões](https://github.com/JARVISS-project/Jarvis/releases).
Confira `SHA256SUMS.txt`, execute o instalador por usuário e abra o JARVIS.
Na versão portátil, extraia o ZIP inteiro e mantenha `_internal` ao lado do executável.
O pacote não exige Python de desenvolvimento. Microsoft Visual C++ Runtime x64
é um pré-requisito externo. O pacote não possui certificado de assinatura pago.
Se a política do Windows bloquear a execução, use um ambiente autorizado;
não desative proteções para instalar.

## Sua configuração

Em **Ajustes > Conversa e voz**, escolha seu nome, tom e tamanho das respostas.
Comece por **Hoje**, **Agenda** e **Conversa**. Voz e conexões são opcionais.
**Ctrl+K** busca ações e contatos; **Ctrl+1/2/3** abre Hoje/Agenda/Conversa.
**Ctrl+Shift+N** captura uma tarefa ou lembrete local. Aparência, densidade e
atalhos podem ser ajustados em **Ajustes**. Lembretes aparecem no painel do aplicativo.

Para conversa local, instale [Ollama](https://ollama.com/download/windows),
prepare explicitamente o modelo configurado e mantenha o serviço local ativo.
Modelo inicial: `qwen3:4b-instruct-2507-q4_K_M`. Modelos consomem armazenamento
e memória e possuem licenças próprias. APIs externas não são obrigatórias.

Você pode usar qualquer pasta própria de notas Markdown/Obsidian. A integração
fica sem pasta até você escolher uma. Não há nome, estrutura ou coleção de notas
obrigatória. Conceda somente o acesso que desejar; configuração não elimina as
barreiras de autorização, confirmação e auditoria.

Para falar, escolha a voz do Windows ou configure um par Piper ONNX/JSON.
A importação de MP3/WAV prepara uma amostra pessoal para o componente neural
opcional, que não acompanha este instalador. Ela não transforma o MP3 em modelo
Piper. O componente neural e seus modelos precisam estar instalados no ambiente
que executa o JARVIS. Sua amostra nunca acompanha o pacote público.
Para ouvir comandos, configure também um modelo STT e autorize a captura.
Modelos não estão incluídos. GPU/CUDA não está homologada nesta prévia;
o teste CPU com áudio sintético não comprova qualidade em todo microfone/hardware.

## Seus dados

Programa: `%LOCALAPPDATA%\Programs\JARVIS`.
Perfil próprio: `%LOCALAPPDATA%\JARVIS\Desktop`.
O perfil reúne configurações, memória, permissões, cache e logs deste usuário.
Nenhum perfil ou conteúdo pessoal de outra instalação acompanha o programa.
Não existe importação automática de notas ou contas.

Atualizar ou reinstalar preserva o perfil. Desinstalar remove o programa e
preserva seus dados. Faça backup antes de remover manualmente o perfil.
Use **Encerrar JARVIS** e confirme para encerrar a sessão.

## Código e licenças

Código do JARVIS na tag da versão. Fontes de terceiros, receitas e hashes na
mesma página de downloads; avisos completos em `_internal/licenses`.
Bibliotecas Qt são dinâmicas e permitem substituição/relink compatível.
