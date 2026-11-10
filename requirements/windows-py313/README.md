# Dependências fixadas para Windows x64

Perfil CPython 3.13.14 x64. Os arquivos .lock fixam versões e SHA-256.
Consulte ../../docs/DESENVOLVIMENTO.md para instalação e build.
Outros sistemas e versões precisam de validação própria.

O perfil atual usa Torch 2.15.0, Transformers 5.22.2 e Setuptools 84.2.1.
O wheel Torch deste perfil vem do PyPI; ele não provisiona CUDA. Drivers,
CUDA e inferência GPU exigem validação e provisionamento separados.

O extra `neural-voice` exige as mesmas versões mínimas corrigidas. Chatterbox
0.1.7 exige versões anteriores e não resolve com esse extra. Aguarde uma versão
upstream compatível; não ignore o resolvedor nem reduza as versões de segurança.
Isso não altera ambientes locais já instalados, que precisam de migração própria.
