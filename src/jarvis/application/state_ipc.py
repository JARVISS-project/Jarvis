"""Transporte local, somente leitura, para snapshots de estado do Jarvis.

O canal existe apenas para observabilidade. A UI não envia comandos ao Core por
este socket, reduzindo o impacto de uma interface comprometida ou com falha.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import socket
from threading import Event, Lock, Thread
from typing import Any

from jarvis.application.state import AgentState, AgentStateBus, StateSnapshot
from jarvis.application.telemetry import (
    MAX_COUNT,
    MAX_DURATION_MS,
    TelemetryBus,
    TelemetrySnapshot,
)


PROTOCOL_VERSION = 1
LOOPBACK_HOST = "127.0.0.1"
MAX_CLIENTS = 8
OUTBOUND_QUEUE_SIZE = 256


@dataclass(frozen=True, slots=True)
class StateEndpoint:
    """Endereço de um runtime local que publica estado para consumidores."""

    host: str
    port: int
    pid: int
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, int | str]:
        return {
            "host": self.host,
            "port": self.port,
            "pid": self.pid,
            "protocol_version": self.protocol_version,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "StateEndpoint":
        try:
            host = str(value["host"])
            port = int(value["port"])
            pid = int(value["pid"])
            protocol_version = int(value["protocol_version"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Endpoint de estado inválido.") from error

        if host != LOOPBACK_HOST:
            raise ValueError("O endpoint de estado deve usar loopback local.")
        if not 1 <= port <= 65535:
            raise ValueError("Porta do endpoint de estado inválida.")
        if protocol_version != PROTOCOL_VERSION:
            raise ValueError("Versão incompatível do protocolo de estado.")
        return cls(
            host=host,
            port=port,
            pid=pid,
            protocol_version=protocol_version,
        )

    @classmethod
    def read(cls, path: Path) -> "StateEndpoint":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise RuntimeError(
                "Runtime do Jarvis não encontrado para conexão da UI."
            ) from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Endpoint de estado inválido.") from error
        if not isinstance(value, dict):
            raise RuntimeError("Endpoint de estado inválido.")
        try:
            return cls.from_dict(value)
        except ValueError as error:
            raise RuntimeError(str(error)) from error


class LocalStateServer:
    """Publica ``AgentStateBus`` via TCP loopback em formato NDJSON."""

    def __init__(
        self,
        state_bus: AgentStateBus,
        *,
        endpoint_path: Path,
        host: str = LOOPBACK_HOST,
        telemetry_bus: TelemetryBus | None = None,
    ) -> None:
        if host != LOOPBACK_HOST:
            raise ValueError("O servidor de estado só pode usar loopback local.")
        self.state_bus = state_bus
        self.endpoint_path = endpoint_path
        self.host = host
        self.telemetry_bus = telemetry_bus
        self.endpoint: StateEndpoint | None = None
        self._server_socket: socket.socket | None = None
        self._clients: list[socket.socket] = []
        self._clients_lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._send_thread: Thread | None = None
        self._outbound: Queue[bytes] = Queue(maxsize=OUTBOUND_QUEUE_SIZE)
        self._unsubscribe = None
        self._unsubscribe_telemetry = None

    def start(self) -> StateEndpoint:
        if self.endpoint is not None:
            return self.endpoint

        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.host, 0))
        server_socket.listen(4)
        server_socket.settimeout(0.2)
        port = int(server_socket.getsockname()[1])
        self._server_socket = server_socket
        self.endpoint = StateEndpoint(
            host=self.host,
            port=port,
            pid=os.getpid(),
        )
        self._write_endpoint(self.endpoint)
        self._send_thread = Thread(
            target=self._send_loop,
            name="jarvis-state-ipc-writer",
            daemon=True,
        )
        self._send_thread.start()
        self._unsubscribe = self.state_bus.subscribe(
            self._broadcast,
            replay_current=False,
        )
        if self.telemetry_bus is not None:
            self._unsubscribe_telemetry = self.telemetry_bus.subscribe(
                self._broadcast_telemetry,
                replay_current=False,
            )
        self._thread = Thread(
            target=self._accept_loop,
            name="jarvis-state-ipc-server",
            daemon=True,
        )
        self._thread.start()
        return self.endpoint

    def _write_endpoint(self, endpoint: StateEndpoint) -> None:
        self.endpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.endpoint_path.with_suffix(
            self.endpoint_path.suffix + ".tmp"
        )
        temporary.write_text(
            json.dumps(endpoint.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.endpoint_path)

    @staticmethod
    def _encode(snapshot: StateSnapshot) -> bytes:
        # ``detail`` fica deliberadamente fora do IPC para impedir que nomes de
        # arquivos/notas ou outro conteúdo contextual vazem para a camada visual.
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "type": "state",
            "state": snapshot.state.value,
            "sequence": snapshot.sequence,
            "source": snapshot.source,
        }
        return (json.dumps(payload, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )

    @staticmethod
    def _encode_telemetry(snapshot: TelemetrySnapshot) -> bytes:
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "type": "telemetry",
            "sequence": snapshot.sequence,
            "has_completed_turn": snapshot.has_completed_turn,
            "turn_ms": snapshot.turn_ms,
            "llm_ms": snapshot.llm_ms,
            "llm_calls": snapshot.llm_calls,
            "tool_ms": snapshot.tool_ms,
            "tool_calls": snapshot.tool_calls,
            "audit_events": snapshot.audit_events,
            "audit_write_ok": snapshot.audit_write_ok,
        }
        return (json.dumps(payload, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )

    def _accept_loop(self) -> None:
        server_socket = self._server_socket
        if server_socket is None:
            return
        while not self._stop_event.is_set():
            try:
                client, address = server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            if address[0] != LOOPBACK_HOST:
                client.close()
                continue
            client.settimeout(0.05)
            with self._clients_lock:
                if len(self._clients) >= MAX_CLIENTS:
                    client.close()
                    continue
                self._clients.append(client)
                try:
                    client.sendall(self._encode(self.state_bus.snapshot))
                    if self.telemetry_bus is not None:
                        client.sendall(
                            self._encode_telemetry(self.telemetry_bus.snapshot)
                        )
                except OSError:
                    self._clients.remove(client)
                    client.close()

    def _broadcast(self, snapshot: StateSnapshot) -> None:
        self._enqueue_payload(self._encode(snapshot))

    def _broadcast_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        self._enqueue_payload(self._encode_telemetry(snapshot))

    def _enqueue_payload(self, payload: bytes) -> None:
        try:
            self._outbound.put_nowait(payload)
        except Full:
            try:
                self._outbound.get_nowait()
            except Empty:
                pass
            try:
                self._outbound.put_nowait(payload)
            except Full:
                pass

    def _send_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = self._outbound.get(timeout=0.1)
            except Empty:
                continue
            self._send_payload(payload)

    def _send_payload(self, payload: bytes) -> None:
        dead_clients: list[socket.socket] = []
        with self._clients_lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                client.sendall(payload)
            except OSError:
                dead_clients.append(client)
        with self._clients_lock:
            for client in dead_clients:
                if client in self._clients:
                    self._clients.remove(client)
                client.close()

    def close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._unsubscribe_telemetry is not None:
            self._unsubscribe_telemetry()
            self._unsubscribe_telemetry = None
        self._stop_event.set()
        server_socket = self._server_socket
        self._server_socket = None
        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass
        with self._clients_lock:
            clients = tuple(self._clients)
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._send_thread is not None:
            self._send_thread.join(timeout=1.0)
            self._send_thread = None
        self._remove_own_endpoint()
        self.endpoint = None

    def _remove_own_endpoint(self) -> None:
        endpoint = self.endpoint
        if endpoint is None or not self.endpoint_path.exists():
            return
        try:
            current = StateEndpoint.read(self.endpoint_path)
        except RuntimeError:
            return
        if current.pid == endpoint.pid and current.port == endpoint.port:
            try:
                self.endpoint_path.unlink()
            except OSError:
                pass

    def __enter__(self) -> "LocalStateServer":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class LocalStateClient:
    """Replica snapshots do runtime em um ``AgentStateBus`` da interface."""

    def __init__(
        self,
        state_bus: AgentStateBus,
        endpoint: StateEndpoint,
        *,
        reconnect_seconds: float = 0.35,
        telemetry_bus: TelemetryBus | None = None,
    ) -> None:
        if reconnect_seconds <= 0:
            raise ValueError("reconnect_seconds deve ser positivo.")
        self.state_bus = state_bus
        self.endpoint = endpoint
        self.reconnect_seconds = reconnect_seconds
        self.telemetry_bus = telemetry_bus
        self._stop_event = Event()
        self._socket_lock = Lock()
        self._socket: socket.socket | None = None
        self._thread: Thread | None = None
        self._last_state_sequence = -1

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = Thread(
            target=self._run,
            name="jarvis-state-ipc-client",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                connection = socket.create_connection(
                    (self.endpoint.host, self.endpoint.port),
                    timeout=0.6,
                )
                connection.settimeout(None)
            except OSError:
                self._stop_event.wait(self.reconnect_seconds)
                continue

            with self._socket_lock:
                self._socket = connection
            try:
                self._read_connection(connection)
            finally:
                with self._socket_lock:
                    if self._socket is connection:
                        self._socket = None
                try:
                    connection.close()
                except OSError:
                    pass

            self._stop_event.wait(self.reconnect_seconds)

    def _read_connection(self, connection: socket.socket) -> None:
        buffer = b""
        while not self._stop_event.is_set():
            try:
                chunk = connection.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                self._handle_line(raw_line)

    def _handle_line(self, raw_line: bytes) -> None:
        try:
            payload = json.loads(raw_line.decode("utf-8"))
            if payload.get("protocol_version") != PROTOCOL_VERSION:
                return
            message_type = payload.get("type")
            if message_type == "telemetry":
                self._handle_telemetry(payload)
                return
            if message_type != "state":
                return
            state = AgentState(str(payload["state"]))
            source = str(payload.get("source") or "runtime")
            sequence = self._metric(payload, "sequence", MAX_COUNT)
        except (UnicodeError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            return
        if sequence <= self._last_state_sequence:
            return
        self._last_state_sequence = sequence
        self.state_bus.publish(
            state,
            source=f"ipc:{source}",
        )

    def _handle_telemetry(self, payload: dict[str, Any]) -> None:
        telemetry_bus = self.telemetry_bus
        if telemetry_bus is None:
            return
        try:
            snapshot = TelemetrySnapshot(
                sequence=self._metric(payload, "sequence", MAX_COUNT),
                has_completed_turn=self._boolean(payload, "has_completed_turn"),
                turn_ms=self._metric(payload, "turn_ms", MAX_DURATION_MS),
                llm_ms=self._metric(payload, "llm_ms", MAX_DURATION_MS),
                llm_calls=self._metric(payload, "llm_calls", MAX_COUNT),
                tool_ms=self._metric(payload, "tool_ms", MAX_DURATION_MS),
                tool_calls=self._metric(payload, "tool_calls", MAX_COUNT),
                audit_events=self._metric(payload, "audit_events", MAX_COUNT),
                audit_write_ok=self._optional_boolean(payload, "audit_write_ok"),
            )
            if snapshot.sequence < telemetry_bus.snapshot.sequence:
                return
            telemetry_bus.publish_snapshot(snapshot)
        except (KeyError, TypeError, ValueError):
            return

    @staticmethod
    def _metric(payload: dict[str, Any], name: str, maximum: int) -> int:
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError
        if not 0 <= value <= maximum:
            raise ValueError
        return value

    @staticmethod
    def _boolean(payload: dict[str, Any], name: str) -> bool:
        value = payload[name]
        if not isinstance(value, bool):
            raise TypeError
        return value

    @classmethod
    def _optional_boolean(
        cls,
        payload: dict[str, Any],
        name: str,
    ) -> bool | None:
        value = payload[name]
        if value is None:
            return None
        if not isinstance(value, bool):
            raise TypeError
        return value

    def close(self) -> None:
        self._stop_event.set()
        with self._socket_lock:
            connection = self._socket
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def __enter__(self) -> "LocalStateClient":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
