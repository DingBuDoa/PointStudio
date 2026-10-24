"""Probe the selected runtime in a child process, without retaining a GUI CUDA context."""
from pathlib import Path
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time

from paths import APP, runtime_environment

_cache = {}
_lock = threading.Lock()
POLICY_VERSION = 'adaptive-memory-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def settings_signature(options):
    keys = ('fps', 'max_size', 'overlap', 'cuda_decode', 'camera_mode')
    values = {key: getattr(options, key) for key in keys}
    values['policy_version'] = POLICY_VERSION
    if options.image_scale != 1:
        values['image_scale'] = options.image_scale
    if options.export_sequence:
        values['export_sequence'] = True
        values['sequence_original'] = options.sequence_original
    if options.backend == 'fastmap':
        values['color'] = options.color
    elif options.glomap_gpu:
        values['gpu_min_images'] = options.gpu_min_images
    return values


def probe_local():
    machine = platform.node()
    cpu = platform.processor()
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography') as key:
                machine += winreg.QueryValueEx(key, 'MachineGuid')[0]
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                cpu = winreg.QueryValueEx(key, 'ProcessorNameString')[0].strip()
        except OSError:
            pass
    try:
        import psutil
        ram = psutil.virtual_memory().total
    except ImportError:
        ram = 0
    profile = {'machine_key': digest(machine), 'cpu': cpu, 'threads': os.cpu_count(),
               'ram_bytes': ram, 'os': platform.platform(), 'python': platform.python_version(),
               'cuda_available': False, 'cuda_usable': False, 'devices': []}
    try:
        import torch
        profile.update(torch=torch.__version__, cuda_runtime=torch.version.cuda)
        profile['cuda_available'] = torch.cuda.is_available()
        for index in range(torch.cuda.device_count()):
            prop = torch.cuda.get_device_properties(index)
            profile['devices'].append({'index': index, 'name': prop.name,
                                      'total_bytes': prop.total_memory,
                                      'capability': [prop.major, prop.minor]})
        if profile['cuda_available']:
            free, total = torch.cuda.mem_get_info(0)
            profile.update(free_bytes=free, total_bytes=total)
            # is_available alone does not establish that this build supports the GPU.
            value = torch.ones(1, device='cuda:0') + 1
            profile['cuda_usable'] = value.item() == 2
            del value
    except Exception as error:
        profile['error'] = str(error)[-400:]
    stable = {key: value for key, value in profile.items()
              if key not in ('free_bytes', 'error', 'cuda_usable')}
    profile['fingerprint'] = digest(stable)
    return profile


def get_hardware(python, force=False):
    path = str(Path(python).resolve())
    with _lock:
        cached = _cache.get(path)
        if not force and cached and time.monotonic() - cached[0] < 60:
            return dict(cached[1])
        environment = os.environ.copy()
        environment.update(runtime_environment())
        try:
            result = subprocess.run([path, '-X', 'utf8', str(APP / 'hardware.py'), '--probe'],
                                    env=environment, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=45,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if result.returncode:
                raise RuntimeError(result.stderr[-400:] or '硬件检测进程失败')
            profile = json.loads(result.stdout.strip().splitlines()[-1])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired, RuntimeError) as error:
            return {'error': str(error), 'cuda_usable': False}
        _cache[path] = (time.monotonic(), profile)
        return dict(profile)


def describe_hardware(profile):
    devices = profile.get('devices', [])
    if not devices:
        return 'CUDA 不可用 · ' + profile.get('error', '未检测到兼容 NVIDIA GPU')
    gpu = devices[0]
    text = f"GPU 0 · {gpu['name']} · 显存 {gpu['total_bytes']/2**30:.1f} GiB"
    if 'free_bytes' in profile:
        text += f" / 检测时可用 {profile['free_bytes']/2**30:.1f} GiB"
    text += ' · CUDA 实算通过' if profile.get('cuda_usable') else ' · CUDA 实算未通过'
    return text


if __name__ == '__main__' and '--probe' in sys.argv:
    print(json.dumps(probe_local(), ensure_ascii=False))
