# Dependências fixadas para Windows x64

Perfil CPython 3.13.14 x64. Os arquivos .lock fixam versões e SHA-256.
Consulte ../../docs/DESENVOLVIMENTO.md para instalação e build.
Outros sistemas e versões precisam de validação própria.

O perfil atual usa Torch 2.15.0, Transformers 5.22.2 e Setuptools 84.2.1.
O wheel Torch deste perfil vem do PyPI; ele não provisiona CUDA. Drivers,
CUDA e inferência GPU exigem validação e provisionamento separados.

O extra `neural-voice` exige as mesmas versões mínimas corrigidas. Chatterbox
0.1.7 exige versões anteriores e não resolve diretamente com esse extra.
O preparador opcional usa variantes locais identificadas, com patches e fontes
fixados em `packaging/neural/variants.lock.json`; não são releases upstream.

Para criar um ambiente separado a partir do checkout, usando CPython 3.13.14 x64:

```powershell
python scripts/setup_neural_environment.py --destination data/neural-desktop
```

Abra `Iniciar-JARVIS.cmd` na pasta criada. Nenhum ambiente existente é atualizado.
O perfil inicial é separado, em `%LOCALAPPDATA%/JARVIS/NeuralDesktop`, sem contas,
amostras ou notas. Importe sua própria amostra nas preferências de voz.
Use `--model-directory CAMINHO` para indicar um snapshot multilingual local
completo do Chatterbox, adquirido separadamente; nenhum modelo é baixado pelo
preparador. Sem modelo local, a interface informa a ausência sem mudar de voz.

O padrão usa CPU. `--cuda` seleciona explicitamente Torch CUDA 13.2 e exige GPU,
driver e memória compatíveis; não instala drivers nem troca para CPU em falhas.
A carga CUDA copia os tensores por partes para reduzir o pico de RAM.
Os perfis `neural.lock` e `neural-cuda.lock` compartilham versões e hashes em
`neural-common.lock`. O preparador verifica os wheels originais e produz as
variantes determinísticas, preservando licenças e a marca d'água do fornecedor.

Esse componente executa o código-fonte; não está incluído no instalador `.exe`
leve. Não compartilhe a pasta do ambiente ou seu perfil: publique o código e
as receitas, para que cada pessoa configure sua própria instalação.
