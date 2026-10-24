"""All application-owned paths stay relative to this distribution directory."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'
RUNTIME = ROOT / 'runtime'
PYTHON = RUNTIME / 'python/python.exe'
FASTMAP = RUNTIME / 'fastmap'
CONFIG = ROOT / 'config'
LOGS = ROOT / 'logs'
OUTPUTS = ROOT / 'output'
LEGACY_OUTPUTS = ROOT / 'outputs'
CUDA = RUNTIME / 'cuda'
BINARY_DIRS = {'01 GLOMAP': RUNTIME / 'colmap', '01 GLOMAP GPU': RUNTIME / 'glomap-gpu', '03 FFMPEG': RUNTIME / 'ffmpeg'}


def saved_path(value, previous_root=None):
    """Resolve portable paths and migrate application-owned legacy absolute paths."""
    path = Path(value)
    if not path.is_absolute():
        return ROOT / path
    if previous_root and isinstance(previous_root, (str, os.PathLike)):
        try:
            return ROOT / path.relative_to(Path(previous_root))
        except ValueError:
            pass
    # Older distributions persisted absolute paths without their install root.
    # Only relocate paths inside a named application directory, never media.
    for index in range(len(path.parts) - 1, -1, -1):
        if path.parts[index].casefold() in {ROOT.name.casefold(), 'glomap gui'}:
            return ROOT.joinpath(*path.parts[index + 1:])
    return path


def portable_path(value):
    path = Path(value).absolute()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def runtime_environment():
    # Keep solver DLL namespaces separate: original COLMAP and GPU Ceres differ.
    return {'PYTHONNOUSERSITE': '1', 'PYTHONPATH': '',
            'TCL_LIBRARY': str(RUNTIME / 'python/tcl/tcl8.6'),
            'TK_LIBRARY': str(RUNTIME / 'python/tcl/tk8.6'),
            'PATH': str(CUDA) + os.pathsep + os.environ.get('PATH', '')}
