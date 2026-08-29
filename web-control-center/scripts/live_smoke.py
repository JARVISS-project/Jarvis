"""Executa o cliente Web real contra Gateway WebSocket e Core fake loopback."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis.application.state import AgentStateBus  # noqa: E402
from jarvis.application.telemetry import TelemetryBus  # noqa: E402
from jarvis.remote.auth import DeviceRegistry, PairingManager  # noqa: E402
from jarvis.remote.core_port import (  # noqa: E402
    RemoteConfirmation,
    RemoteSecuritySnapshot,
)
from jarvis.remote.gateway import RemoteGateway  # noqa: E402
from jarvis.remote.websocket_transport import WebSocketTransportServer  # noqa: E402


class SmokeCorePort:
    def process_chat(self, text: str) -> str:
        if text != "live control center probe":
            raise ValueError("Mensagem inesperada no smoke test")
        return "live core response"

    def process_confirmation(
        self,
        *,
        confirmed: bool,
        session_id: str,
        challenge_id: str,
    ) -> str:
        del confirmed, session_id, challenge_id
        return "unused"

    def current_confirmation(self) -> RemoteConfirmation | None:
        return None

    def current_security(self) -> RemoteSecuritySnapshot:
        return RemoteSecuritySnapshot(
            kill_switch_tripped=False,
            capabilities=(
                ("browser", False),
                ("desktop_control", False),
                ("desktop_read", True),
                ("filesystem_read", True),
                ("filesystem_write", False),
                ("obsidian_read", True),
                ("obsidian_write", False),
                ("vision", True),
            ),
        )


def main() -> int:
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm não encontrado")
    with tempfile.TemporaryDirectory(prefix="jarvis-web-live-") as directory:
        temporary = Path(directory)
        pairing_manager = PairingManager(temporary / "pairing.json")
        gateway = RemoteGateway(
            core_port=SmokeCorePort(),
            state_bus=AgentStateBus(),
            telemetry_bus=TelemetryBus(),
            host="127.0.0.1",
            port=0,
            device_registry=DeviceRegistry(temporary / "devices.json"),
            pairing_manager=pairing_manager,
            transport_server=WebSocketTransportServer(
                "127.0.0.1",
                0,
                allowed_origins=("http://localhost:3000",),
            ),
        )
        challenge = pairing_manager.create_pairing("Control Center Live Test")
        gateway.start()
        try:
            address = gateway.bound_address
            if address is None:
                raise RuntimeError("Gateway não iniciou")
            environment = os.environ.copy()
            environment["JARVIS_LIVE_GATEWAY_URL"] = (
                f"ws://{address[0]}:{address[1]}"
            )
            environment["JARVIS_LIVE_PAIRING_CODE"] = challenge.pairing_code
            result = subprocess.run(
                [npm, "test", "--", "--run", "tests/liveGateway.test.ts"],
                cwd=ROOT / "web-control-center",
                env=environment,
                check=False,
            )
            return result.returncode
        finally:
            gateway.stop()
            time.sleep(0.05)


if __name__ == "__main__":
    raise SystemExit(main())
