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


class CapabilityPolicy:
    def __init__(self, permissions: dict[Capability, bool] | None = None) -> None:
        self._permissions = dict(permissions or {})

    def is_enabled(self, capability: Capability) -> bool:
        return self._permissions.get(capability, False)

    def require(self, capability: Capability) -> None:
        if not self.is_enabled(capability):
            raise PermissionError(
                f"Capacidade desabilitada: {capability.value}."
            )

    def require_tool(self, tool_name: str) -> None:
        capability = TOOL_CAPABILITIES.get(tool_name)
        if capability is not None:
            self.require(capability)


TOOL_CAPABILITIES = {
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
    "vision_observe_screen_change": Capability.VISION,
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
    "google_calendar_list_events": Capability.GOOGLE_CALENDAR_READ,
    "google_calendar_get_event": Capability.GOOGLE_CALENDAR_READ,
    "google_calendar_check_availability": Capability.GOOGLE_CALENDAR_READ,
}
