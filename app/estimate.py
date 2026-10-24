"""Conservative ranges from comparable local runs; never benchmark promises."""
import json
import math
import statistics

from paths import CONFIG
from hardware import get_hardware, settings_signature
from workload import describe_workload
from json_store import atomic_write_json

HISTORY = CONFIG / 'task-history.json'


def read_samples():
    try:
        samples = json.loads(HISTORY.read_text(encoding='utf-8'))
        return samples if isinstance(samples, list) else []
    except (OSError, ValueError):
        return []


def save_sample(result):
    # Preserve untagged older records, but never use them as local calibration.
    samples = read_samples()
    samples.append({'schema': 2, 'backend': result['backend'],
                    'frames': result['database']['images'],
                    'seconds': result['task_wall_seconds'] if 'task_wall_seconds' in result else result['mapping_wall_seconds'],
                    'existing_scene': result.get('existing_scene', True),
                    'hardware_key': result.get('hardware', {}).get('fingerprint'),
                    'settings': result.get('estimate_settings'),
                    'workload': result.get('workload'),
                    'gpu_solver_active': result.get('gpu_solver_active')})
    atomic_write_json(HISTORY, samples[-80:])


def comparable(sample, backend, hardware_key, settings, workload):
    if not isinstance(sample, dict) or sample.get('schema') != 2 or not hardware_key:
        return False
    if sample.get('backend') != backend or sample.get('hardware_key') != hardware_key or sample.get('settings') != settings:
        return False
    if backend in ('fastmap', 'glomap-gpu') and sample.get('gpu_solver_active') is not True:
        return False
    for key in ('frames', 'seconds'):
        if not isinstance(sample.get(key), (int, float)) or not math.isfinite(sample[key]) or sample[key] <= 0:
            return False
    old = sample.get('workload')
    if not isinstance(old, dict):
        return False
    if any(old.get(key) != workload.get(key) for key in ('stages', 'existing_scene', 'width', 'height', 'codec')):
        return False
    # Frame scaling is only a rough approximation within a similar scene size.
    ratio = workload['frames'] / sample['frames']
    if not 0.5 <= ratio <= 2:
        return False
    if workload.get('pairs') is not None:
        if not isinstance(old.get('pairs'), (int, float)) or old['pairs'] <= 0:
            return False
        density_ratio = (workload['pairs'] / workload['frames']) / (old['pairs'] / sample['frames'])
        if not 0.5 <= density_ratio <= 2:
            return False
    return True


def duration_text(seconds):
    # Avoid implying second-level precision for an uncertain estimate.
    if seconds >= 60:
        return f'{math.ceil(seconds/60)} 分钟'
    return f'{max(5, math.ceil(seconds/5)*5)} 秒'


def estimate_jobs(jobs, options):
    if not jobs:
        return '预估任务时长：添加视频后自动估算'
    hardware = get_hardware(options.python)
    if not hardware.get('fingerprint'):
        return '预估任务时长：暂不可用（硬件检测未完成或失败）'
    if not hardware.get('cuda_usable'):
        return '预估任务时长：暂不可用（CUDA 实算未通过，请检查运行环境）'
    samples = read_samples()
    backend = 'fastmap' if options.backend == 'fastmap' else ('glomap-gpu' if options.glomap_gpu else 'glomap-cpu')
    settings = settings_signature(options)
    lower, upper, unknown, unreadable, least_samples = 0.0, 0.0, 0, 0, 5
    for job in jobs:
        try:
            workload = describe_workload(job['path'], options, job['scene'])
            matching = [sample for sample in samples
                        if comparable(sample, backend, hardware['fingerprint'], settings, workload)][-5:]
            if not matching:
                unknown += 1
                continue
            least_samples = min(least_samples, len(matching))
            predictions = [sample['seconds'] * workload['frames'] / sample['frames'] for sample in matching]
            median = statistics.median(predictions)
            # Heuristic band; this is not a statistical confidence interval.
            low_factor, high_factor = (0.5, 2.0) if len(matching) < 3 else (0.7, 1.5)
            lower += min(median * low_factor, min(predictions) * 0.8)
            upper += max(median * high_factor, max(predictions) * 1.2)
        except Exception:
            unreadable += 1
    notes = []
    if lower:
        notes.append(f'本机同参数相近规模记录；至少 {least_samples} 次参考')
        notes.append('粗估，非保证' if least_samples >= 3 else '样本少，误差可能较大')
    if unknown:
        notes.append(f'{unknown} 项未校准：需本机同参数、相近规模和缓存状态的完成记录')
    if unreadable:
        notes.append(f'{unreadable} 项无法读取素材信息')
    if not lower:
        return '预估任务时长：暂不可用（' + '；'.join(notes) + '）'
    qualifier = '可估部分约' if unknown or unreadable else '约'
    return f'预估任务时长：{qualifier} {duration_text(lower)}–{duration_text(upper)}（' + '；'.join(notes) + '）'
