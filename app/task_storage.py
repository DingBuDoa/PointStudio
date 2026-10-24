"""Restore independent queue records without losing healthy neighboring jobs."""
import json
from pathlib import Path

from paths import saved_path
from tracker_core import Options
from dataclasses import asdict
from project_library import validate_result


def restore_jobs(path, previous_root=None):
    jobs, warnings = {}, []
    try:
        entries = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(entries, list):
            raise ValueError('任务记录需要是列表')
    except FileNotFoundError:
        return jobs, warnings
    except (OSError, ValueError) as error:
        return jobs, [f'无法读取任务记录：{error}']
    for index, job in enumerate(entries, 1):
        try:
            if not isinstance(job, dict):
                raise ValueError('无效的任务记录')
            for key in ('id', 'path', 'name', 'state'):
                if not isinstance(job.get(key), str) or not job[key]:
                    raise ValueError(f'缺少有效的 {key}')
            if job['id'] in jobs:
                raise ValueError('任务标识重复')
            if job['state'] not in ('等待', '排队', '运行中', '完成', '失败', '已停止'):
                raise ValueError('未知任务状态')
            for key in ('error', 'export_error', 'stage', 'created_at'):
                if key in job and not isinstance(job[key], str):
                    raise ValueError(f'无效的 {key}')
            options = Options(**job['options']).validate()
            for key in ('python', 'output_root'):
                setattr(options, key, str(saved_path(getattr(options, key), previous_root)))
            job['options'] = asdict(options)
            job.setdefault('scene', False)
            if type(job['scene']) is not bool:
                raise ValueError('无效的场景标记')
            for key in ('project_dir', 'scene_path'):
                if job.get(key): job[key] = str(saved_path(job[key], previous_root))
            result = validate_result(job.get('result', {}))
            validate_result({'solver_warnings': job.get('solver_warnings', [])})
            for key in ('model', 'scene', 'sequence', 'project_dir', 'images_dir', 'log_dir'):
                if result.get(key): result[key] = str(saved_path(result[key], previous_root))
            for warning in [*job.get('solver_warnings', []), *result.get('solver_warnings', [])]:
                warning['log_path'] = str(saved_path(warning['log_path'], previous_root))
            if job['state'] in ('运行中', '排队'): job['state'] = '等待'
            jobs[job['id']] = job
        except (ValueError, TypeError, KeyError) as error:
            warnings.append(f'第 {index} 条任务记录已跳过：{error}')
    return jobs, warnings
