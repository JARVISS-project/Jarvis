import pytest
from unittest.mock import Mock, MagicMock
import asyncio

from jarvis.tools.browser import BrowserExtractMultipleTool, BrowserGetSnapshotTool, BrowserWaitForSelectorTool
from jarvis.tools.desktop import DesktopListApplicationsTool
from jarvis.tools.obsidian import ObsidianDeleteNoteTool
from jarvis.tools.vision import VisionExtractTextTool, VisionGetUITreeTool
from jarvis.security.risk import RiskLevel
from jarvis.tools.executor import ToolExecutor
from jarvis.security.capabilities import CapabilityPolicy, Capability

def test_browser_extract_multiple_nominal() -> None:
    service = Mock()
    service.list_tabs_sync.return_value = [Mock(is_active=True, id="1")]
    service.extract_multiple.return_value = []
    tool = BrowserExtractMultipleTool(service)
    assert tool.risk == RiskLevel.READ_ONLY
    assert tool.parameters["required"] == ["selector", "attributes"]

    executor = ToolExecutor(Mock(), Mock(), capability_policy=CapabilityPolicy({Capability.BROWSER: True}))
    result = executor.execute_with_result(tool, tab_id="1")
    assert "Argumento obrigatório ausente" in result.content

def test_browser_get_snapshot_nominal() -> None:
    service = Mock()
    service.list_tabs_sync.return_value = [Mock(is_active=True, id="1")]
    async def snapshot():
        return Mock(url="a", title="b", tabs=[], selection="")
    service.get_page_snapshot.side_effect = lambda _tab_id: snapshot()
    tool = BrowserGetSnapshotTool(service)
    assert tool.risk == RiskLevel.READ_ONLY
    tool.execute()
    service.get_page_snapshot.assert_called_once_with("1")

def test_browser_wait_for_selector_nominal() -> None:
    service = Mock()
    service.list_tabs_sync.return_value = [Mock(is_active=True, id="1")]
    async def found():
        return Mock(success=True)
    service.wait_for_selector.side_effect = lambda *_args: found()
    tool = BrowserWaitForSelectorTool(service)
    assert tool.risk == RiskLevel.READ_ONLY
    tool.execute(selector="div")
    service.wait_for_selector.assert_called_once_with("1", "div", 30)

def test_desktop_list_applications_nominal() -> None:
    service = Mock()
    service.list_applications.return_value = []
    tool = DesktopListApplicationsTool(service)
    assert tool.risk == RiskLevel.READ_ONLY
    tool.execute()
    service.list_applications.assert_called_once()

def test_obsidian_delete_note_nominal() -> None:
    vault = MagicMock()
    vault.resolve_note.return_value = "note.md"
    tool = ObsidianDeleteNoteTool(vault)
    assert tool.risk == RiskLevel.DESTRUCTIVE
    tool.execute(path="note.md")
    vault.delete_note.assert_called_once_with("note.md")

def test_vision_extract_text_nominal() -> None:
    service = Mock()
    tool = VisionExtractTextTool(service)
    assert tool.risk == RiskLevel.SENSITIVE
    service.extract_text.return_value = Mock(confidence=0.9, text="hello")
    tool.execute(left=0, top=0, right=10, bottom=10)
    service.extract_text.assert_called_once()

def test_vision_get_ui_tree_nominal() -> None:
    service = Mock()
    tool = VisionGetUITreeTool(service)
    assert tool.risk == RiskLevel.SENSITIVE

    mock_element = Mock(name="mock_el", control_type="window", children=[])
    mock_element.bounding_rect = Mock(left=0, top=0, right=10, bottom=10)
    service.get_ui_tree.return_value = Mock(root=mock_element)

    tool.execute()
    service.get_ui_tree.assert_called_once()
