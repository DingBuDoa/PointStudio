"""ASCII directory aliases for Windows native tools with narrow SQLite paths."""
from pathlib import Path
import ctypes
import os
import tempfile

from paths import ROOT


def _short_path(path):
    if os.name != 'nt':
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return buffer.value if 0 < length < len(buffer) and buffer.value.isascii() else None


class NativePaths:
    """Keep directory junctions alive until the native child has exited."""
    def __init__(self):
        self.directory = None
        self.aliases = {}

    def __enter__(self):
        return self

    def _workspace(self):
        if self.directory is not None:
            return self.directory
        candidates = [Path(tempfile.gettempdir()), ROOT]
        failures = []
        for candidate in candidates:
            ascii_base = str(candidate) if str(candidate).isascii() else _short_path(candidate)
            if not ascii_base:
                continue
            try:
                self.directory = Path(tempfile.mkdtemp(prefix='autotracker-native-', dir=ascii_base))
                return self.directory
            except OSError as error:
                failures.append(str(error))
        raise RuntimeError('无法创建原生工具的兼容路径。请将程序解压到可写的英文目录（例如 C:\\GLOMAP-GUI），'
                           '或将 TEMP 设置到可写的英文目录。' + ' '.join(failures))

    def path(self, value):
        text = str(value)
        if os.name != 'nt' or text.isascii() or not Path(text).is_absolute():
            return text
        original = Path(text)
        short = _short_path(original)
        if short:
            return short
        ancestor = original
        suffix = []
        while not ancestor.is_dir():
            if ancestor.parent == ancestor:
                raise RuntimeError(f'兼容路径没有可访问的父目录：{original}')
            suffix.insert(0, ancestor.name)
            ancestor = ancestor.parent
        if any(not part.isascii() for part in suffix):
            raise RuntimeError(f'原生工具的文件名需要英文或数字：{original}')
        key = os.path.normcase(str(ancestor))
        if key not in self.aliases:
            import _winapi
            alias = self._workspace() / f'path-{len(self.aliases)}'
            _winapi.CreateJunction(str(ancestor), str(alias))
            self.aliases[key] = alias
        return str(self.aliases[key].joinpath(*suffix))

    def command(self, args, cwd, env):
        return ([self.path(value) for value in args], self.path(cwd),
                {**env, 'PATH': os.pathsep.join(self.path(p) if Path(p).is_dir() else p
                                               for p in env.get('PATH', '').split(os.pathsep))})

    def __exit__(self, exc_type, exc, traceback):
        # Remove the links themselves only; never recursively delete targets.
        for alias in reversed(list(self.aliases.values())):
            try:
                os.rmdir(alias)
            except OSError:
                pass
        if self.directory is not None:
            try:
                self.directory.rmdir()
            except OSError:
                pass
