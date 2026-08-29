# JARVIS Web Control Center

Aplicação React separada da Ambient UI. Ela se comunica exclusivamente pelo
Remote Gateway:

```text
Web Control Center -> WebSocket -> Remote Gateway -> RemoteCorePort -> Core
```

O frontend não possui acesso direto a Harness, Tools, filesystem, processos ou
Desktop. As telas de segurança e observabilidade são somente leitura.

## Desenvolvimento local

No host do JARVIS, gere um código one-time e inicie o Gateway permitindo a
origem exata do Vite:

```powershell
python -m jarvis --remote-pair --remote-device-name "Web Control Center"
python -m jarvis --remote --remote-transport websocket `
  --remote-websocket-origin http://127.0.0.1:5173
```

Em outro terminal:

```powershell
cd web-control-center
npm install
npm run dev -- --host 127.0.0.1
```

Acesse `http://127.0.0.1:5173`, conecte a `ws://127.0.0.1:8766` e informe o
código criado localmente. Para `npm run preview`, permita a origem exata da
porta exibida pelo Vite, normalmente `http://127.0.0.1:4173`.

Use WSS e uma origem explícita para qualquer cenário fora de loopback. Exposição
pública na Internet não faz parte desta fundação.

## Validação

```powershell
npm test -- --run
npm run build
python scripts/live_smoke.py
```

O smoke test inicia Gateway e Core fake somente em loopback, cria o pairing pelo
caminho local e exercita o cliente TypeScript real até a resposta de chat.
