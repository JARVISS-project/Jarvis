"""CLI e composição do único runtime JARVIS."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from jarvis.application.state import AgentStateBus
from jarvis.application.state_ipc import LocalStateServer
from jarvis.application.telemetry import TelemetryBus
from jarvis.application.voice_bridge import VoiceStateBridge
from jarvis.config import JarvisConfig
from jarvis.context.recent import RecentContext
from jarvis.remote.core_port import HarnessRemoteCorePort
from jarvis.remote.gateway import RemoteGateway
from jarvis.remote.websocket_transport import WebSocketTransportServer
from jarvis.runtime.ambient_ui import launch_ambient_ui, stop_spawned_ambient_ui
from jarvis.runtime.health import RuntimeHealthStore
from jarvis.runtime.instance import (
    InstanceAlreadyRunningError,
    SingleInstanceGuard,
    process_is_running,
)
from jarvis.runtime.lifecycle import (
    RuntimeCapabilities,
    RuntimeLifecycle,
    RuntimeState,
)
from jarvis.runtime.logging import configure_runtime_logging
from jarvis.runtime.resource_manager import ResourceManager
from jarvis.runtime.startup import StartupOptions, WindowsStartupManager
from jarvis.security.audit import AuditLogger


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jarvis")
    parser.add_argument("--voice", action="store_true")
    parser.add_argument("--ui", action="store_true")
    parser.add_argument(
        "--background", "--daemon",
        action="store_true",
        help="Inicia o runtime sem manter uma janela de console.",
    )
    parser.add_argument("--background-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--runtime-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--runtime-status", action="store_true")
    parser.add_argument("--runtime-stop", action="store_true")
    google_group = parser.add_mutually_exclusive_group()
    google_group.add_argument(
        "--google", choices=("connect", "status", "disconnect")
    )
    google_group.add_argument(
        "--google-calendar", choices=("connect", "status", "disconnect")
    )
    google_group.add_argument(
        "--google-calendar-write", choices=("connect", "status", "disconnect")
    )
    parser.add_argument("--google-client-config", type=Path)
    parser.add_argument("--google-timeout", type=float, default=120.0)
    parser.add_argument("--startup", choices=("install", "remove", "status"))
    parser.add_argument("--startup-voice", action="store_true")
    parser.add_argument("--startup-ui", action="store_true")
    parser.add_argument("--startup-remote", action="store_true")
    parser.add_argument("--wake-word-model-path", type=Path)
    parser.add_argument("--microphone-device-index", type=int)
    parser.add_argument("--wake-word-threshold", type=float)
    parser.add_argument("--tts-pace", choices=("fast", "natural", "calm"))
    parser.add_argument("--setup-voice", action="store_true")
    parser.add_argument("--setup-neural-voice", action="store_true")
    parser.add_argument("--setup-fast-voice", action="store_true")
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--remote-host", default="127.0.0.1")
    parser.add_argument("--remote-port", type=int, default=8766)
    parser.add_argument("--remote-transport", choices=("tcp", "websocket"), default="tcp")
    parser.add_argument("--remote-websocket-origin", action="append", default=[])
    parser.add_argument("--remote-allow-lan", action="store_true")
    parser.add_argument("--remote-cert", type=Path)
    parser.add_argument("--remote-key", type=Path)
    parser.add_argument("--remote-pair", action="store_true")
    parser.add_argument("--remote-device-name", default="Remote Device")
    parser.add_argument("--remote-devices", action="store_true")
    parser.add_argument("--remote-revoke", metavar="DEVICE_ID")
    return parser


def main(argv: list[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = argument_parser().parse_args(raw_argv)
    if args.runtime_root is not None:
        os.chdir(args.runtime_root.resolve())
    config = JarvisConfig()
    if args.tts_pace is not None:
        config.tts_piper_pace = args.tts_pace

    if args.startup:
        _validate_startup_args(args)
        raise SystemExit(_handle_startup(args))
    if args.runtime_status or args.runtime_stop:
        raise SystemExit(_handle_runtime_command(args))
    if args.google or args.google_calendar or args.google_calendar_write:
        raise SystemExit(_handle_google_command(args))
    if args.setup_voice or args.setup_neural_voice or args.setup_fast_voice:
        _handle_setup(args, config)
        return
    if args.remote_pair or args.remote_devices or args.remote_revoke:
        from jarvis.__main__ import _handle_remote_command

        _handle_remote_command(args, config)
        return
    _validate_runtime_args(args)
    if args.background and not args.background_child:
        _launch_background_child(raw_argv)
        print("JARVIS solicitado em background.")
        return
    if args.voice or args.remote or args.ui or args.background_child:
        _run_managed_runtime(args, config)
        return
    _run_console_runtime(config)


def _validate_runtime_args(args: argparse.Namespace) -> None:
    if args.wake_word_threshold is not None and not 0.0 <= args.wake_word_threshold <= 1.0:
        raise SystemExit("--wake-word-threshold deve estar entre 0.0 e 1.0.")
    if args.remote_transport != "websocket" and args.remote_websocket_origin:
        raise SystemExit("--remote-websocket-origin requer --remote-transport websocket.")
    if args.remote_allow_lan and not args.remote:
        raise SystemExit("--remote-allow-lan requer --remote.")
    if args.remote_host not in {"127.0.0.1", "localhost", "::1"} and not args.remote_allow_lan:
        raise SystemExit("Exposição LAN requer --remote-allow-lan.")


def _validate_startup_args(args: argparse.Namespace) -> None:
    remote = args.startup_remote or args.remote
    if args.remote_websocket_origin and args.remote_transport != "websocket":
        raise SystemExit("--remote-websocket-origin requer --remote-transport websocket.")
    if args.remote_allow_lan and not remote:
        raise SystemExit("--remote-allow-lan requer Remote no startup.")
    if remote and args.remote_host not in {"127.0.0.1", "localhost", "::1"}:
        if not args.remote_allow_lan:
            raise SystemExit("Exposição LAN requer --remote-allow-lan.")
    if (args.remote_cert is None) != (args.remote_key is None):
        raise SystemExit("--remote-cert e --remote-key devem ser usados juntos.")


def _runtime_paths() -> tuple[Path, Path, Path]:
    directory = Path("data/runtime")
    return (
        directory / "instance.json",
        directory / "runtime-status.json",
        Path("logs/runtime.log"),
    )


def _handle_google_command(args: argparse.Namespace) -> int:
    from jarvis.integrations.google.errors import GoogleIntegrationError
    from jarvis.integrations.google.factory import (
        build_google_auth_manager,
        build_google_auth_manager_with_calendar,
        build_google_auth_manager_with_calendar_write,
    )
    from jarvis.integrations.google.models import ConnectionState

    if not 5.0 <= args.google_timeout <= 600.0:
        print("--google-timeout deve estar entre 5 e 600 segundos.")
        return 1
    try:
        calendar_command = args.google_calendar is not None
        write_command = args.google_calendar_write is not None
        command = args.google_calendar_write or args.google_calendar or args.google
        if write_command:
            manager = build_google_auth_manager_with_calendar_write()
        elif calendar_command:
            manager = build_google_auth_manager_with_calendar()
        else:
            manager = build_google_auth_manager()
        if command == "connect":
            client_config = args.google_client_config
            if client_config is None:
                configured = os.getenv("JARVIS_GOOGLE_CLIENT_CONFIG")
                client_config = Path(configured) if configured else None
            if client_config is None:
                print(
                    "Informe uma credencial OAuth Desktop local com "
                    "--google-client-config ou JARVIS_GOOGLE_CLIENT_CONFIG."
                )
                return 1
            print("Abrindo o navegador para consentimento Google explícito...")
            status = manager.connect(
                client_config,
                timeout_seconds=args.google_timeout,
            )
        elif command == "disconnect":
            status = manager.disconnect()
            if status.error_code == "remote_revocation_unconfirmed":
                print(
                    "Credencial local bloqueada; a revogação remota permanece "
                    "pendente e pode ser tentada novamente."
                )
                return 1
            if status.error_code == "remote_revocation_unconfirmed_local_removed":
                print(
                    "Credencial local removida, mas a revogação remota não "
                    "foi confirmada; revise as permissões da conta Google."
                )
                return 1
        else:
            status = manager.status()
    except GoogleIntegrationError as error:
        print(str(error))
        return 1

    messages = {
        ConnectionState.DISCONNECTED: "Google desconectado.",
        ConnectionState.AUTHORIZATION_REQUIRED: "Autorização Google necessária.",
        ConnectionState.CONNECTED: "Google conectado.",
        ConnectionState.TOKEN_REFRESHING: "Credencial Google sendo renovada.",
        ConnectionState.REVOKED: "Credencial Google revogada; conecte novamente.",
        ConnectionState.ERROR: "Integração Google indisponível.",
    }
    if write_command:
        prefix = "Google Calendar Write"
    elif calendar_command:
        prefix = "Google Calendar"
    else:
        prefix = "Google"
    print(messages[status.state].replace("Google", prefix, 1))
    return 0 if status.state in {ConnectionState.CONNECTED, ConnectionState.DISCONNECTED} else 1


def _run_managed_runtime(args: argparse.Namespace, config: JarvisConfig) -> None:
    from jarvis.__main__ import (
        _start_desktop_kill_switch,
        build_harness,
        build_voice_pipeline,
    )

    instance_path, status_path, log_path = _runtime_paths()
    if args.background_child:
        configure_runtime_logging(log_path)
    logger = logging.getLogger("jarvis.runtime")
    guard = SingleInstanceGuard(instance_path)
    try:
        guard.acquire()
    except InstanceAlreadyRunningError as error:
        raise SystemExit(str(error)) from error

    capabilities = RuntimeCapabilities(
        voice=bool(args.voice), remote=bool(args.remote), ui=bool(args.ui)
    )
    logger.info(
        "Runtime starting capabilities=%s",
        ",".join(capabilities.enabled()) or "core",
    )
    started_at = time.time()
    health = RuntimeHealthStore(status_path)
    stop_event = threading.Event()
    last_startup_error: list[str | None] = [None]
    lifecycle = RuntimeLifecycle(
        lambda state: health.publish(
            state=state,
            instance_id=guard.instance_id,
            started_at=started_at,
            capabilities=capabilities,
            last_startup_error=last_startup_error[0],
        )
    )
    lifecycle.begin_startup()
    lifecycle.add_finalizer(guard.release)
    lifecycle.add_finalizer(health.clear_stop_request)
    previous_handlers = _install_signal_handlers(stop_event.set)
    lifecycle.add_cleanup(lambda: _restore_signal_handlers(previous_handlers))

    harness = None
    pipeline = None
    voice_thread = None
    voice_error: list[BaseException] = []
    try:
        state_bus = AgentStateBus()
        telemetry_bus = TelemetryBus()
        resource_manager = ResourceManager()
        resource_manager.idle()
        kill_switch, kill_listener = _start_desktop_kill_switch(config)
        if kill_listener is not None:
            lifecycle.add_cleanup(kill_listener.stop)

        def create_harness(confirmation=None, recent_context=None):
            nonlocal harness
            if harness is not None:
                return harness
            harness = build_harness(
                config,
                resource_manager,
                confirmation=confirmation,
                recent_context=recent_context,
                state_bus=state_bus,
                desktop_kill_switch=kill_switch,
                telemetry_bus=telemetry_bus,
            )
            lifecycle.add_cleanup(harness.close)
            return harness

        if args.voice:
            pipeline = build_voice_pipeline(
                config=config,
                model_path=args.wake_word_model_path,
                microphone_device_index=args.microphone_device_index,
                state_callback=VoiceStateBridge(state_bus),
                wake_word_threshold=args.wake_word_threshold,
                state_bus=state_bus,
                desktop_kill_switch=kill_switch,
                telemetry_bus=telemetry_bus,
                resource_manager=resource_manager,
                harness_builder=create_harness,
            )

            def stop_voice() -> None:
                pipeline.request_stop()
                if voice_thread is not None and voice_thread is not threading.current_thread():
                    # Active work is drained rather than forcefully interrupted.
                    voice_thread.join()

            lifecycle.add_cleanup(stop_voice)
        else:
            create_harness(None, RecentContext())

        if args.remote:
            gateway = _build_gateway(args, config, harness, kill_switch, state_bus, telemetry_bus)
            gateway.start()
            lifecycle.add_cleanup(gateway.stop)

        if args.ui:
            state_server = LocalStateServer(
                state_bus,
                endpoint_path=config.ambient_ui_endpoint_path,
                telemetry_bus=telemetry_bus,
            )
            state_server.start()
            lifecycle.add_cleanup(state_server.close)
            ui_process = launch_ambient_ui(config.ambient_ui_endpoint_path)
            lifecycle.add_cleanup(lambda: stop_spawned_ambient_ui(ui_process))

        if pipeline is not None:
            def run_voice() -> None:
                try:
                    pipeline.run()
                except BaseException as error:
                    voice_error.append(error)
                finally:
                    stop_event.set()

            voice_thread = threading.Thread(
                target=run_voice,
                name="jarvis-voice-mode",
                daemon=False,
            )
            voice_thread.start()

        lifecycle.mark_running()
        logger.info("Runtime running")
        while not stop_event.wait(0.5):
            if health.stop_requested(guard.instance_id):
                stop_event.set()
                break
            health.publish(
                state=RuntimeState.RUNNING,
                instance_id=guard.instance_id,
                started_at=started_at,
                capabilities=capabilities,
            )
        if voice_error:
            raise RuntimeError("Voice pipeline encerrou inesperadamente.") from voice_error[0]
    except KeyboardInterrupt:
        stop_event.set()
    except BaseException as error:
        safe_error = type(error).__name__
        last_startup_error[0] = safe_error
        logger.exception("Falha agregada durante startup/runtime: %s", safe_error)
        lifecycle.stop()
        raise
    finally:
        if lifecycle.state is not RuntimeState.STOPPED:
            logger.info("Runtime stopping")
            errors = lifecycle.stop()
            for error in errors:
                logger.error("Falha de cleanup: %s", type(error).__name__)


def _build_gateway(args, config, harness, kill_switch, state_bus, telemetry_bus):
    transport = None
    if args.remote_transport == "websocket":
        transport = WebSocketTransportServer(
            host=args.remote_host,
            port=args.remote_port,
            certfile=str(args.remote_cert) if args.remote_cert else None,
            keyfile=str(args.remote_key) if args.remote_key else None,
            allow_lan=args.remote_allow_lan,
            allowed_origins=args.remote_websocket_origin,
        )
    return RemoteGateway(
        core_port=HarnessRemoteCorePort(harness, kill_switch),
        state_bus=state_bus,
        telemetry_bus=telemetry_bus,
        audit=AuditLogger(config.audit_log_path),
        host=args.remote_host,
        port=args.remote_port,
        certfile=str(args.remote_cert) if args.remote_cert else None,
        keyfile=str(args.remote_key) if args.remote_key else None,
        allow_lan=args.remote_allow_lan,
        transport_server=transport,
    )


def _run_console_runtime(config: JarvisConfig) -> None:
    from jarvis.__main__ import _start_desktop_kill_switch, build_harness

    instance_path, _status_path, _log_path = _runtime_paths()
    with SingleInstanceGuard(instance_path):
        kill_switch, listener = _start_desktop_kill_switch(config)
        harness = build_harness(
            config,
            ResourceManager(),
            desktop_kill_switch=kill_switch,
        )
        try:
            harness.run()
        finally:
            harness.close()
            if listener is not None:
                listener.stop()


def _launch_background_child(raw_argv: list[str]) -> None:
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if sys.platform == "win32" and pythonw.exists():
        executable = pythonw
    child_args = [arg for arg in raw_argv if arg != "--background"]
    child_args.append("--background-child")
    if "--runtime-root" not in child_args:
        child_args.extend(["--runtime-root", str(Path.cwd())])
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [str(executable), "-m", "jarvis", *child_args],
        creationflags=flags,
        close_fds=True,
    )


def _handle_startup(args: argparse.Namespace) -> int:
    if sys.platform != "win32":
        print("Startup no login está disponível apenas no Windows.")
        return 1
    manager = WindowsStartupManager()
    if args.startup == "remove":
        manager.remove()
        print("Startup do JARVIS removido.")
        return 0
    if args.startup == "status":
        command = manager.status()
        print("Startup instalado." if command else "Startup não instalado.")
        return 0
    options = StartupOptions(
        voice=args.startup_voice or args.voice,
        remote=args.startup_remote or args.remote,
        ui=args.startup_ui or args.ui,
        remote_transport=args.remote_transport,
        remote_host=args.remote_host,
        remote_port=args.remote_port,
        remote_websocket_origins=tuple(args.remote_websocket_origin),
        remote_allow_lan=args.remote_allow_lan,
        remote_cert=args.remote_cert,
        remote_key=args.remote_key,
    )
    manager.install(options, Path.cwd())
    print("Startup do JARVIS instalado para o usuário atual.")
    return 0


def _handle_runtime_command(args: argparse.Namespace) -> int:
    _instance_path, status_path, _log_path = _runtime_paths()
    store = RuntimeHealthStore(status_path)
    current = store.read()
    active = (
        current is not None
        and current.state in {RuntimeState.STARTING.value, RuntimeState.RUNNING.value}
        and process_is_running(current.pid)
    )
    if args.runtime_stop:
        if not active or current is None:
            print("Runtime do JARVIS não está ativo.")
            return 1
        store.request_stop(current.instance_id)
        print("Solicitação de parada enviada.")
        return 0
    if not active or current is None:
        suffix = (
            f" Último erro de startup: {current.last_startup_error}."
            if current is not None and current.last_startup_error
            else ""
        )
        print(f"Runtime inativo.{suffix}")
        return 1
    capabilities = ", ".join(current.capabilities) or "core"
    print(
        f"Runtime ativo | PID {current.pid} | uptime {current.uptime_seconds}s | "
        f"capacidades: {capabilities}"
    )
    return 0


def _handle_setup(args: argparse.Namespace, config: JarvisConfig) -> None:
    from jarvis.__main__ import setup_fast_voice, setup_neural_voice, setup_voice_model

    if args.setup_voice:
        setup_voice_model(config.wake_word_model)
    elif args.setup_neural_voice:
        setup_neural_voice(config)
    else:
        setup_fast_voice(config)


def _install_signal_handlers(callback):
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if signum is None:
            continue
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda _signum, _frame: callback())
        except (ValueError, OSError):
            continue
    return previous


def _restore_signal_handlers(previous) -> None:
    for signum, handler in previous.items():
        try:
            signal.signal(signum, handler)
        except (ValueError, OSError):
            pass
