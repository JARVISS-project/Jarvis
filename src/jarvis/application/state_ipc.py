"""Loopback snapshots plus bounded PTT intent, with no Tool authority."""

from __future__ import annotations

from dataclasses import dataclass, field
import base64
import json
import os
import select
from pathlib import Path
from queue import Empty, Full, Queue
import socket
from threading import Event, Lock, RLock, Thread
from typing import Any, Callable
from jarvis.application.administration import valid_admin_command
from jarvis.security.permissions import PermissionStore
from jarvis.application.text_interaction import (
    MAX_TEXT_BYTES, TextEventBus, TextInteractionController, valid_text_command,
)

from jarvis.application.audio_activity import (
    AudioActivityBus,
    AudioActivitySnapshot,
)
from jarvis.application.conversation_log import (
    ConversationEntry,
    ConversationLogBus,
    ConversationLogSnapshot,
)
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
MAX_CONVERSATION_ENTRIES = 12
MAX_CONVERSATION_TEXT_CHARS = 2000
MAX_COMMAND_BYTES = 256
PTT_COMMANDS = ("ptt_start", "ptt_stop")


def valid_ptt_command(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and set(payload) == {"command"}
        and isinstance(payload["command"], str)
        and payload["command"] in PTT_COMMANDS
    )


@dataclass(frozen=True, slots=True)
class StateEndpoint:
    """Endereço de um runtime local que publica estado para consumidores."""

    host: str
    port: int
    pid: int
    protocol_version: int = PROTOCOL_VERSION
    admin_secret: str | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, int | str]:
        result = {
            "host": self.host,
            "port": self.port,
            "pid": self.pid,
            "protocol_version": self.protocol_version,
        }
        if self.admin_secret is not None:
            result["admin_secret"] = self.admin_secret
        return result

    def administrative_authorization(self) -> str:
        if self.admin_secret is None:
            raise PermissionError("Runtime não oferece administração autenticada.")
        try:
            from jarvis.integrations.google.storage import WindowsDPAPIProtector
            token = WindowsDPAPIProtector().unprotect(base64.b64decode(self.admin_secret, validate=True)).decode("ascii")
            if len(token) != 64 or any(c not in "0123456789abcdef" for c in token):
                raise ValueError
            return token
        except (RuntimeError, ValueError, UnicodeError, OSError):
            raise PermissionError("Administração local não autenticada.") from None

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
        secret = value.get("admin_secret")
        if secret is not None and (not isinstance(secret, str) or len(secret) > 4096):
            raise ValueError("Credencial administrativa de runtime inválida.")
        return cls(
            host=host,
            port=port,
            pid=pid,
            protocol_version=protocol_version,
            admin_secret=secret,
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
        audio_activity_bus: AudioActivityBus | None = None,
        conversation_log_bus: ConversationLogBus | None = None,
        command_callback: Callable[[dict[str, Any]], None] | None = None,
        text_controller: TextInteractionController | None = None,
        admin_controller=None,
    ) -> None:
        if host != LOOPBACK_HOST:
            raise ValueError("O servidor de estado só pode usar loopback local.")
        self.state_bus = state_bus
        self.endpoint_path = endpoint_path
        self.host = host
        self.telemetry_bus = telemetry_bus
        self.audio_activity_bus = audio_activity_bus
        self.conversation_log_bus = conversation_log_bus
        self.command_callback = command_callback
        self.text_controller = text_controller
        self.admin_controller = admin_controller
        self._write_lock = RLock()
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
        self._unsubscribe_audio_activity = None
        self._unsubscribe_conversation_log = None

    def start(self) -> StateEndpoint:
        if self.endpoint is not None:
            return self.endpoint
        self._stop_event.clear()
        admin_secret = None
        if self.admin_controller is not None:
            from jarvis.integrations.google.storage import WindowsDPAPIProtector
            admin_secret = base64.b64encode(WindowsDPAPIProtector().protect(self.admin_controller.authorization.encode("ascii"))).decode("ascii")

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
            admin_secret=admin_secret,
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
        if self.audio_activity_bus is not None:
            self._unsubscribe_audio_activity = (
                self.audio_activity_bus.subscribe(
                    self._broadcast_audio_activity,
                    replay_current=False,
                )
            )
        if self.conversation_log_bus is not None:
            self._unsubscribe_conversation_log = (
                self.conversation_log_bus.subscribe(
                    self._broadcast_conversation_log,
                    replay_current=False,
                )
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

    @staticmethod
    def _encode_audio_activity(snapshot: AudioActivitySnapshot) -> bytes:
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "type": "audio_activity",
            "sequence": snapshot.sequence,
            "level": snapshot.level,
            "peak": snapshot.peak,
        }
        return (json.dumps(payload, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )

    @staticmethod
    def _encode_conversation_log(snapshot: ConversationLogSnapshot) -> bytes:
        entries = [
            {
                "role": entry.role,
                "text": entry.text[:MAX_CONVERSATION_TEXT_CHARS],
                "sequence": entry.sequence,
            }
            for entry in snapshot.entries[-MAX_CONVERSATION_ENTRIES:]
        ]
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "type": "conversation_log",
            "sequence": snapshot.sequence,
            "entries": entries,
        }
        return (json.dumps(payload, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )

    def _accept_loop(self) -> None:
        server_socket = self._server_socket
        if server_socket is None:
            return
        buffers: dict[socket.socket, bytes] = {}
        ptt_owner: socket.socket | None = None

        def drop(client: socket.socket) -> None:
            nonlocal ptt_owner
            buffers.pop(client, None)
            if self.text_controller is not None:
                self.text_controller.disconnect(client)
            if self.admin_controller is not None:
                self.admin_controller.disconnect(client)
            if ptt_owner is client:
                self._handle_client_command(b'{"command":"ptt_stop"}')
                ptt_owner = None
            with self._clients_lock:
                if client in self._clients:
                    self._clients.remove(client)
            client.close()

        while not self._stop_event.is_set():
            with self._clients_lock:
                clients = tuple(self._clients)
            for client in tuple(buffers):
                if client not in clients:
                    drop(client)
            try:
                readables, _, _ = select.select(
                    [server_socket, *clients], [], [], 0.2
                )
            except (OSError, ValueError):
                continue

            for r in readables:
                if r is server_socket:
                    try:
                        client, address = server_socket.accept()
                    except (socket.timeout, OSError):
                        continue

                    if address[0] != LOOPBACK_HOST:
                        client.close()
                        continue

                    client.settimeout(0.05)
                    with self._clients_lock:
                        if len(self._clients) >= MAX_CLIENTS:
                            client.close()
                            continue
                        self._clients.append(client)
                        buffers[client] = b""
                        try:
                            with self._write_lock:
                                client.sendall(self._encode(self.state_bus.snapshot))
                                if self.telemetry_bus is not None:
                                    client.sendall(
                                        self._encode_telemetry(self.telemetry_bus.snapshot)
                                    )
                                if self.audio_activity_bus is not None:
                                    client.sendall(
                                        self._encode_audio_activity(
                                            self.audio_activity_bus.snapshot
                                        )
                                    )
                                if self.conversation_log_bus is not None:
                                    client.sendall(
                                        self._encode_conversation_log(
                                            self.conversation_log_bus.snapshot
                                        )
                                    )
                                if self.text_controller is not None:
                                    self._reply_text(client, self.text_controller.hello())
                        except OSError:
                            self._clients.remove(client)
                            client.close()
                else:
                    try:
                        data = r.recv(MAX_TEXT_BYTES + 1)
                        if not data:
                            drop(r)
                        else:
                            pending = buffers.get(r, b"") + data
                            while b"\n" in pending:
                                line, pending = pending.split(b"\n", 1)
                                payload = self._decode_text_command(line)
                                if payload is not None and payload.get("command") == "admin_request" and self.admin_controller is not None:
                                    self.admin_controller.handle(payload, lambda event, client=r: self._reply_text(client, event), owner=r)
                                    continue
                                if payload is not None and self.text_controller is not None:
                                    self.text_controller.handle(
                                        r, payload, lambda event, client=r: self._reply_text(client, event)
                                    )
                                    continue
                                payload = self._decode_command(line)
                                if payload is None:
                                    drop(r)
                                    break
                                command = payload["command"]
                                if command == "ptt_start" and ptt_owner is None:
                                    ptt_owner = r
                                    self._handle_client_command(line)
                                elif command == "ptt_stop" and ptt_owner is r:
                                    self._handle_client_command(line)
                                    ptt_owner = None
                            if r in buffers:
                                if len(pending) > (MAX_TEXT_BYTES if self.text_controller else MAX_COMMAND_BYTES):
                                    drop(r)
                                else:
                                    buffers[r] = pending
                    except OSError:
                        drop(r)
        if ptt_owner is not None:
            self._handle_client_command(b'{"command":"ptt_stop"}')

    @staticmethod
    def _decode_text_command(data: bytes) -> dict[str, Any] | None:
        if len(data) > MAX_TEXT_BYTES:
            return None
        try:
            payload = json.loads(data.decode("utf-8"), object_pairs_hook=PermissionStore._pairs)
            return payload if valid_text_command(payload) or valid_admin_command(payload) else None
        except (UnicodeError, ValueError, TypeError, RecursionError):
            return None

    def _reply_text(self, client: socket.socket, event: dict[str, Any]) -> None:
        event = {**event, "protocol_version": PROTOCOL_VERSION}
        try:
            with self._write_lock:
                client.sendall((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            # A partial frame must not leave the UI waiting on a lost response.
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    @staticmethod
    def _decode_command(data: bytes) -> dict[str, str] | None:
        if len(data) > MAX_COMMAND_BYTES:
            return None
        try:
            # Pairs preserve duplicate keys, which must not gain last-key wins.
            pairs = json.loads(data.decode("utf-8"), object_pairs_hook=list)
        except (UnicodeError, ValueError, RecursionError):
            return None
        if not isinstance(pairs, list) or len(pairs) != 1:
            return None
        if not isinstance(pairs[0], tuple) or len(pairs[0]) != 2:
            return None
        payload = dict(pairs)
        return payload if valid_ptt_command(payload) else None

    def _handle_client_command(self, data: bytes) -> None:
        payload = self._decode_command(data)
        if self.command_callback is None or payload is None:
            return
        try:
            self.command_callback(payload)
        except Exception:
            # Neither peer payload nor callback diagnostics enter logs.
            pass

    def _broadcast(self, snapshot: StateSnapshot) -> None:
        self._enqueue_payload(self._encode(snapshot))

    def _broadcast_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        self._enqueue_payload(self._encode_telemetry(snapshot))

    def _broadcast_audio_activity(
        self,
        snapshot: AudioActivitySnapshot,
    ) -> None:
        self._enqueue_payload(self._encode_audio_activity(snapshot))

    def _broadcast_conversation_log(
        self,
        snapshot: ConversationLogSnapshot,
    ) -> None:
        self._enqueue_payload(self._encode_conversation_log(snapshot))

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
                with self._write_lock:
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
        if self._unsubscribe_audio_activity is not None:
            self._unsubscribe_audio_activity()
            self._unsubscribe_audio_activity = None
        if self._unsubscribe_conversation_log is not None:
            self._unsubscribe_conversation_log()
            self._unsubscribe_conversation_log = None
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
        audio_activity_bus: AudioActivityBus | None = None,
        conversation_log_bus: ConversationLogBus | None = None,
        text_bus: TextEventBus | None = None,
        endpoint_path: Path | None = None,
    ) -> None:
        if reconnect_seconds <= 0:
            raise ValueError("reconnect_seconds deve ser positivo.")
        self.state_bus = state_bus
        self.endpoint = endpoint
        self.reconnect_seconds = reconnect_seconds
        self.telemetry_bus = telemetry_bus
        self.audio_activity_bus = audio_activity_bus
        self.conversation_log_bus = conversation_log_bus
        self.text_bus = text_bus
        self.endpoint_path = endpoint_path
        self._commands: Queue[dict[str, Any]] = Queue(maxsize=8)
        self._command_thread: Thread | None = None
        self._stop_event = Event()
        self._socket_lock = Lock()
        self._socket: socket.socket | None = None
        self._thread: Thread | None = None
        self._last_state_sequence = -1
        self._last_audio_activity_sequence = -1
        self._last_conversation_log_sequence = -1

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = Thread(
            target=self._run,
            name="jarvis-state-ipc-client",
            daemon=True,
        )
        self._thread.start()

        self._command_thread = Thread(target=self._send_commands,
                                      name="jarvis-ui-command-writer", daemon=True)
        self._command_thread.start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.endpoint_path is not None:
                    current = StateEndpoint.read(self.endpoint_path)
                    if current != self.endpoint:
                        self.endpoint = current
                        self._last_state_sequence = -1
                        self._last_audio_activity_sequence = -1
                        self._last_conversation_log_sequence = -1
                connection = socket.create_connection(
                    (self.endpoint.host, self.endpoint.port),
                    timeout=0.6,
                )
                connection.settimeout(None)
            except (OSError, RuntimeError):
                self._stop_event.wait(self.reconnect_seconds)
                continue

            with self._socket_lock:
                self._socket = connection
            try:
                self._read_connection(connection)
            finally:
                if self.text_bus is not None:
                    self.text_bus.publish({"status": "disconnected"})
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
            if message_type in {"text", "admin"}:
                if self.text_bus is not None:
                    self.text_bus.publish(payload)
                return
            if message_type == "telemetry":
                self._handle_telemetry(payload)
                return
            if message_type == "audio_activity":
                self._handle_audio_activity(payload)
                return
            if message_type == "conversation_log":
                self._handle_conversation_log(payload)
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

    def _handle_audio_activity(self, payload: dict[str, Any]) -> None:
        audio_activity_bus = self.audio_activity_bus
        if audio_activity_bus is None:
            return
        try:
            sequence = self._metric(payload, "sequence", MAX_COUNT)
            level = self._unit_float(payload, "level")
            peak = self._unit_float(payload, "peak")
        except (KeyError, TypeError, ValueError):
            return
        if sequence <= self._last_audio_activity_sequence:
            return
        self._last_audio_activity_sequence = sequence
        audio_activity_bus.publish(level, peak=peak)

    def _handle_conversation_log(
        self,
        payload: dict[str, Any],
    ) -> None:
        conversation_log_bus = self.conversation_log_bus
        if conversation_log_bus is None:
            return
        try:
            sequence = self._metric(payload, "sequence", MAX_COUNT)
            raw_entries = payload["entries"]
            if not isinstance(raw_entries, list):
                raise TypeError
            if len(raw_entries) > MAX_CONVERSATION_ENTRIES:
                raise ValueError
            entries: list[ConversationEntry] = []
            for raw_entry in raw_entries:
                if not isinstance(raw_entry, dict):
                    raise TypeError
                role = str(raw_entry["role"])
                if role not in {"user", "assistant"}:
                    raise ValueError
                text_value = raw_entry["text"]
                if not isinstance(text_value, str):
                    raise TypeError
                if len(text_value) > MAX_CONVERSATION_TEXT_CHARS:
                    raise ValueError
                entry_sequence = self._metric(
                    raw_entry,
                    "sequence",
                    MAX_COUNT,
                )
                entries.append(
                    ConversationEntry(
                        role=role,
                        text=text_value,
                        sequence=entry_sequence,
                    )
                )
        except (KeyError, TypeError, ValueError):
            return
        if sequence <= self._last_conversation_log_sequence:
            return
        self._last_conversation_log_sequence = sequence
        conversation_log_bus.publish_snapshot(
            ConversationLogSnapshot(
                entries=tuple(entries),
                sequence=sequence,
            )
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
    def _unit_float(payload: dict[str, Any], name: str) -> float:
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError
        number = float(value)
        if not 0.0 <= number <= 1.0:
            raise ValueError
        return number

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

    def send_command(self, payload: dict[str, Any]) -> None:
        """Bounded enqueue; no socket work on the graphical thread."""
        if not (valid_ptt_command(payload) or valid_text_command(payload) or valid_admin_command(payload)):
            return
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 100 > MAX_TEXT_BYTES:
            if self.text_bus is not None:
                self.text_bus.publish({"type": "admin" if payload.get("command") == "admin_request" else "text",
                    "status": "error", "error": "Pedido acima do limite de tamanho; reduza o conteúdo.",
                    "text": "Pedido acima do limite de tamanho.", "generation": payload.get("generation"), "interaction": payload.get("interaction")})
            return
        try:
            self._commands.put_nowait(dict(payload))
        except Full:
            if self.text_bus is not None:
                self.text_bus.publish({"type": "admin" if payload.get("command") == "admin_request" else "text", "status": "error", "text": "Fila local ocupada.", "error": "Fila local ocupada; pedido não aceito.",
                                       "generation": payload.get("generation"),
                                       "interaction": payload.get("interaction")})

    def _send_commands(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = self._commands.get(timeout=0.1)
            except Empty:
                continue
            with self._socket_lock:
                connection = self._socket
            try:
                if payload.get("command") == "admin_request":
                    payload = {**payload, "authorization": self.endpoint.administrative_authorization()}
                if connection is None:
                    raise OSError
                connection.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            except PermissionError:
                if self.text_bus is not None:
                    self.text_bus.publish({"type": "admin", "generation": payload.get("generation"),
                        "interaction": payload.get("interaction"), "status": "error", "error": "Administração local não autenticada; reabra a UI do runtime atual."})
            except (OSError, RuntimeError, ValueError, UnicodeError):
                if self.text_bus is not None:
                    self.text_bus.publish({"status": "disconnected"})

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

        if self._command_thread is not None:
            self._command_thread.join(timeout=1.0)
            self._command_thread = None

    def __enter__(self) -> "LocalStateClient":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
