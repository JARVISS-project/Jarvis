"""Backend Windows UI Automation para captura de árvore de acessibilidade."""

import sys
from jarvis.vision.models import CaptureRegion, UITreeResult, UIElement


class WindowsUIAutomationBackend:
    def __init__(self) -> None:
        self._uia_available = self._check_uia()

    def _check_uia(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import comtypes.client
            import uiautomation
            return True
        except Exception:
            return False

    def get_ui_tree(self, region: CaptureRegion | None = None) -> UITreeResult:
        if not self._uia_available:
            raise RuntimeError(
                "UI tree requer Windows UI Automation (comtypes, uiautomation)."
            )

        import time
        import uiautomation as auto

        if region is not None:
            elements = auto.ControlFromPoint(
                auto.Point(region.left, region.top)
            )
            if elements is None:
                raise RuntimeError("Nenhum elemento encontrado na região.")
            root_element = elements
        else:
            root_element = auto.GetRootControl()

        def build_ui_element(element: auto.Control) -> UIElement:
            rect = element.BoundingRectangle
            bounding_rect = CaptureRegion(
                left=rect.left,
                top=rect.top,
                right=rect.right,
                bottom=rect.bottom,
            )
            children = []
            try:
                for child in element.GetChildren():
                    children.append(build_ui_element(child))
            except Exception:
                pass

            properties = {}
            try:
                properties["class_name"] = element.ClassName
                properties["automation_id"] = element.AutomationId
                properties["process_id"] = element.ProcessId
                properties["is_visible"] = element.IsVisible
                properties["is_enabled"] = element.IsEnabled
            except Exception:
                pass

            return UIElement(
                name=element.Name,
                control_type=element.ControlTypeName,
                bounding_rect=bounding_rect,
                children=tuple(children),
                properties=properties,
            )

        root = build_ui_element(root_element)
        return UITreeResult(root=root, timestamp=time.time())