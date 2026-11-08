from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from jarvis.config import JarvisConfig


class ConfigCommandCoordinator:
    """Owns deterministic local /config command parsing and persistence."""

    def __init__(
        self,
        config: JarvisConfig,
        obsidian_available: Callable[[], bool],
    ) -> None:
        self.config = config
        self._obsidian_available = obsidian_available

    def process(self, command: str) -> str:
        normalized = command.strip()
        if normalized in {"/config", "/config obsidian"}:
            vault = self.config.obsidian_vault_path
            status = "OK" if self._obsidian_available() else "NOT CONFIGURED"
            vault_name = vault.name if vault is not None else "NOT CONFIGURED"
            write = "ENABLED" if self.config.obsidian_write_enabled else "DISABLED"
            error = (
                f"\nLocal settings: INVALID ({self.config.settings_error})"
                if self.config.settings_error else ""
            )
            return (
                f"Obsidian: {status}\nVault: {vault_name}\nWrite: {write}"
                f"{error}"
            )
    
        vault_match = re.fullmatch(
            r'/config\s+obsidian\s+vault\s+"?(.+?)"?',
            normalized,
            flags=re.IGNORECASE,
        )
        if vault_match is not None:
            vault = Path(vault_match.group(1)).expanduser()
            if not vault.is_dir():
                return "Diretório do Vault não encontrado. Configuração mantida."
            try:
                self.config.save_obsidian_settings(vault=vault)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            return (
                f"Vault '{vault.name}' salvo. Reinicie o Jarvis para "
                "registrar as Tools do Obsidian."
            )
    
        write_match = re.fullmatch(
            r"/config\s+obsidian\s+write\s+(on|off)",
            normalized,
            flags=re.IGNORECASE,
        )
        if write_match is not None:
            enabled = write_match.group(1).casefold() == "on"
            try:
                self.config.save_obsidian_settings(write=enabled)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            state = "habilitada" if enabled else "desabilitada"
            return f"Escrita no Obsidian {state} na configuração local."
    
        if normalized.casefold() == "/config desktop":
            read = "ENABLED" if self.config.desktop_read_enabled else "DISABLED"
            control = (
                "ENABLED" if self.config.desktop_control_enabled else "DISABLED"
            )
            return (
                f"Desktop read: {read}\n"
                f"Desktop control: {control}\n"
                "Kill switch: Ctrl+Alt+K (quando control está habilitado)"
            )
    
        desktop_match = re.fullmatch(
            r"/config\s+desktop\s+(read|control)\s+(on|off)",
            normalized,
            flags=re.IGNORECASE,
        )
        if desktop_match is not None:
            setting = desktop_match.group(1).casefold()
            enabled = desktop_match.group(2).casefold() == "on"
            try:
                if setting == "read":
                    self.config.save_desktop_settings(read=enabled)
                else:
                    self.config.save_desktop_settings(control=enabled)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            state = "habilitado" if enabled else "desabilitado"
            return (
                f"Desktop {setting} {state}. Reinicie o Jarvis para aplicar."
            )
    
        if normalized.casefold() == "/config vision":
            state = "ENABLED" if self.config.vision_enabled else "DISABLED"
            return f"Vision read-only: {state}"
    
        vision_match = re.fullmatch(
            r"/config\s+vision\s+(on|off)",
            normalized,
            flags=re.IGNORECASE,
        )
        if vision_match is not None:
            enabled = vision_match.group(1).casefold() == "on"
            try:
                self.config.save_vision_settings(enabled=enabled)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            state = "habilitada" if enabled else "desabilitada"
            return f"Vision read-only {state}. Reinicie o Jarvis para aplicar."
    
        if normalized.casefold() == "/config google_calendar":
            state = "ENABLED" if self.config.google_calendar_enabled else "DISABLED"
            return f"Google Calendar read-only: {state}"
    
        calendar_match = re.fullmatch(
            r"/config\s+google_calendar\s+(on|off)",
            normalized,
            flags=re.IGNORECASE,
        )
        if calendar_match is not None:
            enabled = calendar_match.group(1).casefold() == "on"
            try:
                self.config.save_calendar_settings(enabled=enabled)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            state = "habilitada" if enabled else "desabilitada"
            return (
                f"Google Calendar read-only {state}. "
                "Reinicie o Jarvis para aplicar."
            )
    
        if normalized.casefold() == "/config google_calendar_write":
            state = "ENABLED" if self.config.google_calendar_write_enabled else "DISABLED"
            return f"Google Calendar write: {state}"
    
        calendar_write_match = re.fullmatch(
            r"/config\s+google_calendar_write\s+(on|off)",
            normalized,
            flags=re.IGNORECASE,
        )
        if calendar_write_match is not None:
            enabled = calendar_write_match.group(1).casefold() == "on"
            try:
                self.config.save_calendar_write_settings(enabled=enabled)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            state = "habilitada" if enabled else "desabilitada"
            return (
                f"Google Calendar write {state}. "
                "Reinicie o Jarvis para aplicar."
            )
    
        if normalized.casefold() == "/config voice":
            reference = self.config.tts_reference_audio_path
            reference_label = str(reference) if reference is not None else "nenhuma"
            piper_ready = (
                self.config.tts_piper_model_path.is_file()
                and self.config.tts_piper_config_path.is_file()
            )
            return (
                f"TTS: {self.config.tts_provider}\n"
                f"Dispositivo neural: {self.config.tts_device}\n"
                f"Idioma: {self.config.tts_language_id}\n"
                f"Voz rápida Piper: {'pronta' if piper_ready else 'não preparada'}\n"
                f"Ritmo Piper: {self.config.tts_piper_pace} "
                f"(length scale {self.config.tts_piper_length_scale:.2f})\n"
                f"Voz de referência: {reference_label}"
            )
    
        mode_match = re.fullmatch(
            r"/config\s+voice\s+mode\s+(fast|quality|auto)",
            normalized,
            flags=re.IGNORECASE,
        )
        if mode_match is not None:
            mode = mode_match.group(1).casefold()
            provider = {
                "fast": "piper",
                "quality": "chatterbox",
                "auto": "auto",
            }[mode]
            try:
                self.config.save_voice_settings(provider=provider)
            except (OSError, ValueError) as error:
                return f"Não foi possível salvar a configuração: {error}."
            return (
                f"Modo de voz '{mode}' salvo ({provider}). "
                "Reinicie o Jarvis para aplicar."
            )
    
        provider_match = re.fullmatch(
            r"/config\s+voice\s+provider\s+(auto|sapi|piper|chatterbox)",
            normalized,
            flags=re.IGNORECASE,
        )
        if provider_match is not None:
            provider = provider_match.group(1).casefold()
            try:
                self.config.save_voice_settings(provider=provider)
            except (OSError, ValueError) as error:
                return f"Não foi possível salvar a configuração: {error}."
            return (
                f"TTS '{provider}' salvo. Reinicie o Jarvis para aplicar."
            )
    
        pace_match = re.fullmatch(
            r"/config\s+voice\s+pace\s+(fast|natural|calm)",
            normalized,
            flags=re.IGNORECASE,
        )
        if pace_match is not None:
            pace = pace_match.group(1).casefold()
            try:
                self.config.save_voice_settings(piper_pace=pace)
            except (OSError, ValueError) as error:
                return f"Não foi possível salvar a configuração: {error}."
            return (
                f"Ritmo Piper alterado para '{pace}'. "
                "Reinicie o Jarvis para aplicar."
            )
    
        reference_off = re.fullmatch(
            r"/config\s+voice\s+reference\s+off",
            normalized,
            flags=re.IGNORECASE,
        )
        if reference_off is not None:
            try:
                self.config.save_voice_settings(clear_reference=True)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            return "Voz de referência removida. Reinicie o Jarvis para aplicar."
    
        reference_match = re.fullmatch(
            r'/config\s+voice\s+reference\s+"?(.+?)"?',
            normalized,
            flags=re.IGNORECASE,
        )
        if reference_match is not None:
            reference = Path(reference_match.group(1)).expanduser()
            if not reference.is_file():
                return "Áudio de referência não encontrado. Configuração mantida."
            try:
                self.config.save_voice_settings(reference_audio=reference)
            except OSError as error:
                return f"Não foi possível salvar a configuração: {error}."
            return (
                f"Voz de referência '{reference.name}' salva. Reinicie o Jarvis "
                "para aplicar."
            )
    
        if normalized.casefold() == "/config persona":
            return (
                "Tratamento: " + self.config.assistant_address_mode + "\n"
                "Estilo: " + self.config.assistant_response_style
            )
    
        treatment_match = re.fullmatch(
            r"/config\s+persona\s+treatment\s+(senhor|natural|direto)",
            normalized,
            flags=re.IGNORECASE,
        )
        if treatment_match is not None:
            treatment = treatment_match.group(1).casefold()
            try:
                self.config.save_persona_settings(address_mode=treatment)
            except (OSError, ValueError) as error:
                return f"Não foi possível salvar a configuração: {error}."
            return f"Modo de tratamento alterado para '{treatment}'."
    
        return (
            "Uso: /config | /config obsidian | /config desktop | "
            "/config google_calendar | /config google_calendar_write | /config voice | /config persona | "
            '/config obsidian vault "C:\\caminho" | '
            "/config obsidian write on|off | "
            "/config desktop read on|off | "
            "/config desktop control on|off | "
            "/config voice provider auto|sapi|piper|chatterbox | "
            "/config voice mode fast|quality|auto | "
            "/config voice pace fast|natural|calm | "
            '/config voice reference "C:\\voz.wav"|off | '
            "/config persona treatment senhor|natural|direto"
        )
