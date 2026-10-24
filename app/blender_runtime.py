"""Discover and probe Blender installations without importing a GUI toolkit."""
from pathlib import Path
import os
import re
import shutil
import subprocess

from paths import ROOT

HIDE_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def probe_blender(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f'Blender 程序不存在：{path}')
    result = subprocess.run([str(path), '--version'], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=20, creationflags=HIDE_WINDOW)
    line = next((line.strip() for line in result.stdout.splitlines() if line.startswith('Blender ')), '')
    if result.returncode or not line:
        raise ValueError(f'无法读取 Blender 版本：{path}')
    match = re.search(r'Blender (\d+)\.(\d+)', line)
    if not match or tuple(map(int, match.groups())) < (4, 0):
        raise ValueError('需要 Blender 4.0 或更新版本（导出使用原生几何节点接口）。')
    return {'path': str(path), 'version': line}


def discover_blenders(settings):
    candidates = [settings.get('blender_path', ''), *settings.get('blender_paths', []),
                  ROOT / 'runtime/blender/blender.exe', shutil.which('blender') or '']
    roots = [Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Blender Foundation',
             Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Steam/steamapps/common']
    # Bound portable installation discovery; never recursively scan a drive.
    for drive in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
        root = Path(f'{drive}:/')
        if not root.is_dir(): continue
        try:
            roots.extend(p for p in root.iterdir() if p.is_dir() and 'blender' in p.name.lower())
        except OSError:
            pass
    for root in roots:
        if not root.is_dir(): continue
        try:
            for pattern in ('blender.exe', '*/blender.exe', '*/*/blender.exe'):
                candidates.extend(root.glob(pattern))
        except OSError:
            pass
    found, seen = [], set()
    for value in candidates:
        if not value: continue
        key = os.path.normcase(str(Path(value).resolve()))
        if key in seen or not Path(value).is_file(): continue
        seen.add(key)
        try:
            found.append(probe_blender(value))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return found
