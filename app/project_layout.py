"""Named deliverables, private solver cache, and legacy result path helpers."""
from pathlib import Path
import re


def safe_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip()[:100].rstrip(' .')
    if not name or name in ('.', '..'):
        return 'project'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.I):
        name = '_' + name
    return name


def create_project(root, name):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    name = safe_name(name)
    for number in range(1, 100000):
        project = root / (name if number == 1 else f'{name} ({number})')
        try:
            project.mkdir()
        except FileExistsError:
            continue
        (project / '_cache').mkdir()
        (project / 'log').mkdir()
        return project
    raise RuntimeError('同名项目过多，请更换项目名称。')


def cache_directory(scene):
    scene = Path(scene)
    return scene / '_cache' if (scene / '_cache').is_dir() else scene


def result_directory(result):
    return Path(result['project_dir']) if result.get('project_dir') else Path(result['model']).parent.parent


def result_report(result):
    return result_directory(result) / ('_cache/result.json' if result.get('layout_version') == 2 else 'result.json')


def blend_filename(name, version):
    match = re.search(r'(\d+)(?:\.(\d+))?', version)
    version = '.'.join(part for part in match.groups() if part is not None) if match else 'unknown'
    return f'{safe_name(name)}_{version}.blend'
