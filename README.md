# JARVIS

Assistente pessoal para Windows. Cada pessoa configura seu próprio nome,
preferências, arquivos, notas, voz e conexões. A IA local usa o modelo inicial
informado abaixo, preparado no computador de cada usuário.

**Gratuito e de código aberto — GPL-3.0-or-later.** Nenhuma chave de API paga é
necessária para conversar usando Ollama local. Serviços externos são opcionais.

[Página do projeto](https://jarviss-project.github.io/Jarvis/) ·
[Política de privacidade](https://jarviss-project.github.io/Jarvis/privacidade.html)

## Começar

1. Baixe o instalador `.exe` em [Releases](https://github.com/JARVISS-project/Jarvis/releases).
   A opção portátil também está disponível: extraia tudo e abra `JARVIS.exe`.
2. Abra o programa e use **Ajustes** para escolher como deseja ser chamado,
   o tom e o tamanho das respostas. A primeira abertura é por texto.
3. Para conversar com IA local, instale [Ollama](https://ollama.com/download/windows)
   e prepare seu modelo. O modelo inicial é `qwen3:4b-instruct-2507-q4_K_M`.
4. Configure somente os recursos que quiser usar. Notas Obsidian são opcionais:
   escolha uma pasta sua; não existe uma estrutura de notas obrigatória.

O download contém o programa e suas dependências de execução, sem perfis,
conversas, notas, contas, credenciais ou modelos pessoais. Cada instalação cria
seu próprio perfil. Microfone, conexões e ações exigem configuração/permissão.

[Instalação e configuração](docs/manual/WINDOWS_EXECUTAVEL.md) ·
[Executar e compilar o código](docs/DESENVOLVIMENTO.md) ·
[Privacidade](docs/PRIVACIDADE.md) · [Limitações](docs/ESTADO.md)

## Estado e limites

Consulte o [relatório completo do estado do projeto](docs/RELATORIO_ESTADO.md).
O login Google incluído na prévia 0.1.4 está restrito às contas de teste;
a opção de cliente próprio exige configuração. Veja [conexão de contas e agenda](docs/manual/GOOGLE.md).
O avanço do login próprio na prévia 0.1.4 está em [Google público: implementação e validação](docs/GOOGLE_PUBLIC_PREVIEW.md).

## Recursos

- Conversa local, preferências, memória e interface em português.
- Voz opcional, rotinas e ferramentas com verificações de autorização.
- Integrações opcionais com notas Markdown, arquivos, aplicativos e Google.
- Permissões e confirmação antes de operações que as exigem.

Esta é uma prévia em desenvolvimento. Windows limpo e compatibilidade CUDA
ainda precisam de validação. O pacote não tem certificado de assinatura pago;
o Windows pode apresentar avisos ou bloqueá-lo conforme suas políticas.

Veja [LICENSE](LICENSE) e [avisos/fontes dos componentes](THIRD_PARTY_NOTICES.md).
