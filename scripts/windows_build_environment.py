"""Keep unrelated developer tooling out of the frozen Windows application."""
import os
import re
from pathlib import Path


def isolated_build_environment(venv: Path, source: Path, python: Path, windows: Path) -> dict:
    environment = dict(os.environ)
    for key in ('PYTHONHOME', 'QT_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
        environment.pop(key, None)
    environment.update(
        PATH=os.pathsep.join(str(p) for p in (venv / 'Scripts', python, python / 'DLLs', windows / 'System32', windows)),
        PYTHONPATH=str(source), PYTHONIOENCODING='utf-8', PYTHONUTF8='1',
    )
    return environment


def select_native_binaries(entries, *, venv: Path, python: Path, windows: Path):
    trusted = (venv.resolve(), python.resolve())
    windows = windows.resolve()
    selected = []
    for entry in entries:
        name, source, kind = entry
        if Path(name).name.lower() in {'qpdf.dll', 'qt6pdf.dll', 'qtvirtualkeyboardplugin.dll', 'qt6virtualkeyboard.dll'}:
            continue  # Not used by this Widgets UI; keep unrelated plugin stacks out.
        origin = Path(source).resolve()
        # OS DLLs are supplied by Windows; do not shadow its ICU with a foreign ABI.
        if origin.is_relative_to(windows):
            continue
        if not any(origin.is_relative_to(root) for root in trusted):
            raise RuntimeError('Native binary outside pinned build environment: ' + Path(name).name)
        if re.fullmatch(r'(?:vcruntime|msvcp)\d+(?:_[a-z0-9_]+)?\.dll', Path(name).name.lower()):
            continue  # Canonical runtime is external; hashed wheel-private DLLs are distinct.
        selected.append(entry)
    return selected
