"""Persist project labels and discover completed runs without loading point clouds."""
from pathlib import Path
from datetime import datetime
import json
import shutil
import uuid
import os
import math

from paths import CONFIG, OUTPUTS, LEGACY_OUTPUTS, saved_path, portable_path
from project_layout import cache_directory
from json_store import atomic_write_json


def validate_result(result):
    """Validate JSON fields consumed by both queue recovery and the project list."""
    if not isinstance(result, dict): raise ValueError('无效的结果记录')
    for key in ('metrics', 'estimate_settings'):
        values = result.get(key, {})
        if values is None and key == 'estimate_settings': continue
        if not isinstance(values, dict): raise ValueError(f'无效的 {key}')
        numeric = ('points', 'registered_images', 'mean_reprojection_error_px') if key == 'metrics' else ('fps', 'image_scale')
        for field in numeric:
            if field in values and (type(values[field]) not in (int, float) or not math.isfinite(values[field])):
                raise ValueError(f'无效的 {key}.{field}')
    for key in ('task_wall_seconds', 'mapping_wall_seconds'):
        if key in result and (type(result[key]) not in (int, float) or not math.isfinite(result[key])):
            raise ValueError(f'无效的 {key}')
    for key in ('model', 'source', 'scene', 'sequence', 'project_dir', 'images_dir', 'log_dir', 'name', 'backend', 'completed_at'):
        if result.get(key) is not None and not isinstance(result[key], str):
            raise ValueError(f'无效的 {key}')
    warnings = result.get('solver_warnings', [])
    if not isinstance(warnings, list) or any(not isinstance(w, dict) or
            any(not isinstance(w.get(key), str) for key in ('code', 'text', 'log_path')) for w in warnings):
        raise ValueError('无效的求解提醒')
    return result


def model_ready(result):
    if result.get('status') != 'complete' or result.get('metrics', {}).get('points', 0) <= 0:
        return False
    model = Path(result.get('model', ''))
    try:
        if not all((model / name).is_file() and (model / name).stat().st_size > 0
                   for name in ('cameras.txt', 'images.txt', 'points3D.txt')):
            return False
        with (model / 'points3D.txt').open(encoding='utf-8') as stream:
            return any(line.strip() and not line.lstrip().startswith('#') for line in stream)
    except OSError:
        return False


class ProjectLibrary:
    def __init__(self, path=None):
        self.path = Path(path) if path else CONFIG / 'project-library.json'
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.data = {}
        for key, default in (('roots', []), ('names', {}), ('exports', {}), ('export_settings', {}), ('created', {})):
            if not isinstance(self.data.get(key), type(default)):
                self.data[key] = default
        previous_root = self.data.get('distribution_root')
        if not isinstance(previous_root, str): previous_root = None
        resolve = lambda value: str(saved_path(value, previous_root))
        valid_path = lambda value: isinstance(value, str) and bool(value.strip())
        self.data['roots'] = list(dict.fromkeys(resolve(p) for p in self.data['roots'] if valid_path(p)))
        for key in ('names', 'created'):
            self.data[key] = {resolve(k): v for k, v in self.data[key].items() if valid_path(k) and isinstance(v, str)}
        self.data['exports'] = {resolve(k): [resolve(p) for p in v if valid_path(p)]
                                for k, v in self.data['exports'].items() if valid_path(k) and isinstance(v, list)}
        self.data['export_settings'] = {resolve(k): v for k, v in self.data['export_settings'].items()
                                        if valid_path(k) and isinstance(v, dict)}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stored = {**self.data, 'distribution_root': str(CONFIG.parent),
                  'roots': [portable_path(p) for p in self.data['roots']],
                  'names': {portable_path(k): v for k, v in self.data['names'].items()},
                  'created': {portable_path(k): v for k, v in self.data['created'].items()},
                  'exports': {portable_path(k): [portable_path(p) for p in v] for k, v in self.data['exports'].items()},
                  'export_settings': {portable_path(k): v for k, v in self.data['export_settings'].items()}}
        atomic_write_json(self.path, stored)

    def add_root(self, root):
        value = str(Path(root).resolve())
        if not Path(value).is_dir():
            raise ValueError('请选择已有项目或输出目录。')
        if value not in self.data['roots']:
            self.data['roots'].append(value)
            self.save()

    def rename(self, record, name):
        name = name.strip()
        if not name:
            raise ValueError('项目名称不能为空。')
        self.data['names'][record['id']] = name
        self.save()

    def record_export(self, model, output, settings=None):
        key = str(Path(model).resolve())
        paths = self.data['exports'].setdefault(key, [])
        value = str(Path(output).resolve())
        if value not in paths:
            paths.append(value)
        if settings:
            self.data['export_settings'][value] = settings
        self.save()

    def scan(self, output_root):
        roots = {str(OUTPUTS.resolve()), str(LEGACY_OUTPUTS.resolve()), str(Path(output_root).resolve()), *self.data['roots']}
        reports = set()
        warnings = []
        # Fixed-depth patterns deliberately avoid traversing extracted images.
        patterns = ('result.json', 'results/*/*/result.json', '*/results/*/*/result.json',
                    '*/result.json', '*/*/result.json', '_cache/result.json')
        for value in roots:
            root = Path(value)
            if not root.is_dir():
                if root not in (OUTPUTS, LEGACY_OUTPUTS):
                    warnings.append(f'目录不存在：{root}')
                continue
            for pattern in patterns:
                try:
                    reports.update(path.resolve() for path in root.glob(pattern)
                                   if '.trash' not in path.parts)
                except OSError as error:
                    warnings.append(str(error))
        records = []
        for report in reports:
            try:
                result = validate_result(json.loads(report.read_text(encoding='utf-8')))
                if not isinstance(result, dict) or not isinstance(result.get('metrics'), dict):
                    raise ValueError('无效的结果记录')
                # Keep moved distributions usable; the report's run owns its model.
                modern = result.get('layout_version') == 2 and report.parent.name == '_cache'
                directory = report.parent.parent if modern else report.parent
                local_model = directory / 'point could' if modern else directory / 'sparse/0'
                result['model'] = str(local_model if modern or local_model.is_dir() else Path(result['model']))
                scene = (directory if modern and not result.get('existing_scene') else
                         report.parent.parents[2] if report.parent.parent.parent.name == 'results' else Path(result.get('scene', report.parent)))
                result['scene'] = str(scene)
                if modern:
                    result.update(project_dir=str(directory), log_dir=str(directory / 'log'))
                    for warning in result.get('solver_warnings', []):
                        warning['log_path'] = str(directory / 'log' / Path(warning['log_path']).name)
                    if not result.get('existing_scene'):
                        result['images_dir'] = str(directory / '_cache/images')
                if result.get('sequence') and (directory / 'video').is_dir():
                    result['sequence'] = str(directory / 'video')
                source = result.get('source', '')
                manifest = cache_directory(scene) / 'source.json'
                if manifest.is_file():
                    source = json.loads(manifest.read_text(encoding='utf-8')).get('source', source)
                key = str(report)
                exports = [Path(value) for value in self.data['exports'].get(str(Path(result['model']).resolve()), [])]
                exports += list(directory.glob('*.blend'))
                exports = sorted({str(path.resolve()) for path in exports if path.is_file()},
                                 key=lambda value:Path(value).stat().st_mtime, reverse=True)
                # This scan runs off the GUI thread; never traverse the input images cache.
                size = 0
                for walked, subdirs, files in os.walk(directory, followlinks=False):
                    subdirs[:] = [name for name in subdirs if name != '_cache' and not (Path(walked) / name).is_symlink()]
                    for name in files:
                        path = Path(walked) / name
                        try:
                            if not path.is_symlink(): size += path.stat().st_size
                        except OSError: pass
                for value in exports:
                    path = Path(value)
                    if not path.is_relative_to(directory):
                        try: size += path.stat().st_size
                        except OSError: pass
                try:
                    created = datetime.strptime(report.parent.name[:15], '%Y%m%d_%H%M%S').isoformat(timespec='seconds')
                except ValueError:
                    created = result.get('created_at', result.get('completed_at', ''))
                records.append({'id':key, 'report':str(report), 'directory':str(directory),
                                'name':self.data['names'].get(key, result.get('name') or Path(source).stem or scene.name),
                                'source':source, 'result':result, 'ready':model_ready(result),
                                'exports':exports, 'completed':result.get('completed_at', ''),
                                'created': self.data['created'].get(key, created), 'bytes': size})
            except (OSError, ValueError, KeyError, TypeError) as error:
                warnings.append(f'{report}: {error}')
        records.sort(key=lambda record:(record['completed'], record['id']), reverse=True)
        return records, warnings

    def archive(self, record):
        """Move only one verified run into scene/.trash; keep source and matches."""
        report = Path(record['report']).resolve()
        if report.parent.name == '_cache' and report.name == 'result.json' and report.is_file():
            data = json.loads(report.read_text(encoding='utf-8'))
            project = report.parent.parent
            if data.get('layout_version') != 2 or project.is_symlink() or report.parent.is_symlink():
                raise ValueError('项目目录无效。')
            trash = project.parent / '.trash'
            trash.mkdir(exist_ok=True)
            if trash.is_symlink():
                raise ValueError('回收目录无效。')
            destination = trash / (project.name + '_' + uuid.uuid4().hex[:6])
            if destination.exists() or not destination.resolve().is_relative_to(trash.resolve()):
                raise ValueError('回收目录无效。')
            (report.parent / 'recovery.json').write_text(json.dumps({'original_directory': str(project),
                'removed_at': datetime.now().isoformat(timespec='seconds')}, ensure_ascii=False, indent=2), encoding='utf-8')
            shutil.move(str(project), str(destination))
            return destination
        run = report.parent
        if run.parent.parent.name != 'results' or report.name != 'result.json' or not report.is_file():
            raise ValueError('目录结构不符合重建结果格式，无法安全移除。')
        scene = run.parents[2]
        if run == scene or not run.is_relative_to(scene):
            raise ValueError('结果目录无效。')
        trash = scene / '.trash'
        trash.mkdir(exist_ok=True)
        destination = trash / (run.parent.name + '_' + run.name + '_' + uuid.uuid4().hex[:6])
        if destination.exists() or not destination.resolve().is_relative_to(trash.resolve()):
            raise ValueError('回收目录无效。')
        (run / 'recovery.json').write_text(json.dumps({'original_directory':str(run),
            'removed_at':datetime.now().isoformat(timespec='seconds')}, ensure_ascii=False, indent=2), encoding='utf-8')
        shutil.move(str(run), str(destination))
        return destination
