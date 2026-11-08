"""Barreira adicional simples para exposição de capacidades."""

from enum import Enum


class Capability(str, Enum):
    FILESYSTEM_READ = "filesystem_read"
    FILESYSTEM_WRITE = "filesystem_write"
    OBSIDIAN_READ = "obsidian_read"
    OBSIDIAN_WRITE = "obsidian_write"
    DESKTOP_READ = "desktop_read"
    DESKTOP_CONTROL = "desktop_control"
    VISION = "vision"
    BROWSER = "browser"
    GOOGLE_CALENDAR_READ = "google_calendar_read"
    GOOGLE_CALENDAR_WRITE = "google_calendar_write"
    GOOGLE_DRIVE_READ = "google_drive_read"
    GOOGLE_DOCS_READ = "google_docs_read"
    GOOGLE_DRIVE_SELECTION = "google_drive_selection"
    LOCAL_CONTROL = "local_control"
    MEDIA_CONTROL = "media_control"
    SAFE_BROWSER = "safe_browser"
    LOCAL_BRIEFING_READ = 'local_briefing_read'
    EXTERNAL_TEXT = 'external_text'


class CapabilityPolicy:
    def __init__(self, permissions: dict[Capability, bool] | None = None, administrative=None) -> None:
        self._permissions = dict(permissions or {})
        self.administrative = administrative

    def is_enabled(self, capability: Capability) -> bool:
        return self._permissions.get(capability, False)

    def require(self, capability: Capability) -> None:
        if capability is Capability.EXTERNAL_TEXT:
            if self.administrative is None:
                raise PermissionError('Delegação exige permissões explícitas na Central.')
            self.administrative.require('model.external', default='block')
            self.administrative.require('delegation.execute', default='block')
            return
        if self.administrative is not None and capability is Capability.VISION:
            self.administrative.require("capture.screen")
            if self.administrative.has_path_restrictions():
                raise PermissionError("Captura indireta bloqueada no escopo protegido.")
        if not self.is_enabled(capability):
            raise PermissionError(
                f"Capacidade desabilitada: {capability.value}."
            )

    def require_tool(self, tool_name: str) -> None:
        capability = TOOL_CAPABILITIES.get(tool_name)
        if capability is not None:
            self.require(capability)


TOOL_CAPABILITIES = {
    'external_text_delegate': Capability.EXTERNAL_TEXT,
    'organization_execute': Capability.FILESYSTEM_WRITE,
    'organization_undo': Capability.FILESYSTEM_WRITE,
    "app_catalog_open": Capability.LOCAL_CONTROL,
    "app_catalog_status": Capability.LOCAL_CONTROL,
    "app_catalog_window": Capability.LOCAL_CONTROL,
    "app_catalog_close": Capability.LOCAL_CONTROL,
    'local_briefing_memory': Capability.LOCAL_BRIEFING_READ,
    "google_drive_picker_validate": Capability.GOOGLE_DRIVE_SELECTION,
    "google_drive_find_files": Capability.GOOGLE_DRIVE_READ,
    "google_drive_select_file": Capability.GOOGLE_DRIVE_READ,
    "google_drive_metadata": Capability.GOOGLE_DRIVE_READ,
    "google_docs_read": Capability.GOOGLE_DOCS_READ,
    "list_files": Capability.FILESYSTEM_READ,
    "read_file": Capability.FILESYSTEM_READ,
    "create_file": Capability.FILESYSTEM_WRITE,
    "edit_file": Capability.FILESYSTEM_WRITE,
    "move_file": Capability.FILESYSTEM_WRITE,
    "delete_file": Capability.FILESYSTEM_WRITE,
    "obsidian_list_notes": Capability.OBSIDIAN_READ,
    "obsidian_read_note": Capability.OBSIDIAN_READ,
    "obsidian_search_notes": Capability.OBSIDIAN_READ,
    "obsidian_open_note": Capability.OBSIDIAN_READ,
    "obsidian_create_note": Capability.OBSIDIAN_WRITE,
    "obsidian_append_note": Capability.OBSIDIAN_WRITE,
    "obsidian_delete_note": Capability.OBSIDIAN_WRITE,
    "desktop_list_windows": Capability.DESKTOP_READ,
    "desktop_list_applications": Capability.DESKTOP_READ,
    "desktop_focus_window": Capability.DESKTOP_CONTROL,
    "desktop_open_application": Capability.DESKTOP_CONTROL,
    "desktop_move_pointer_to_window": Capability.DESKTOP_CONTROL,
    "desktop_press_escape": Capability.DESKTOP_CONTROL,
    "desktop_close_window": Capability.DESKTOP_CONTROL,
    "local_application_open": Capability.LOCAL_CONTROL,
    "local_application_status": Capability.LOCAL_CONTROL,
    "local_application_focus": Capability.LOCAL_CONTROL,
    "local_application_close": Capability.LOCAL_CONTROL,
    "local_media_control": Capability.MEDIA_CONTROL,
    "current_media": Capability.MEDIA_CONTROL,
    "safe_browser_status": Capability.SAFE_BROWSER,
    "safe_browser_destinations": Capability.SAFE_BROWSER,
    "safe_browser_open": Capability.SAFE_BROWSER,
    "safe_web_search": Capability.SAFE_BROWSER,
    "local_volume_control": Capability.LOCAL_CONTROL,
    "browser_list_tabs": Capability.BROWSER,
    "browser_new_tab": Capability.BROWSER,
    "browser_close_tab": Capability.BROWSER,
    "browser_switch_tab": Capability.BROWSER,
    "browser_navigate": Capability.BROWSER,
    "browser_click": Capability.BROWSER,
    "browser_type": Capability.BROWSER,
    "browser_fill": Capability.BROWSER,
    "browser_extract_text": Capability.BROWSER,
    "browser_extract_multiple": Capability.BROWSER,
    "browser_wait_for_selector": Capability.BROWSER,
    "browser_get_snapshot": Capability.BROWSER,
    "vision_extract_text": Capability.VISION,
    "vision_get_ui_tree": Capability.VISION,
    "vision_observe_screen_change": Capability.VISION,
    "google_calendar_list_events": Capability.GOOGLE_CALENDAR_READ,
    "google_calendar_get_event": Capability.GOOGLE_CALENDAR_READ,
    "google_calendar_check_availability": Capability.GOOGLE_CALENDAR_READ,
    "google_calendar_create_event": Capability.GOOGLE_CALENDAR_WRITE,
    "google_calendar_update_event": Capability.GOOGLE_CALENDAR_WRITE,
}
