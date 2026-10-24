"""Manage only known application diagnostic files; never walk point clouds/images."""
from pathlib import Path
import os
import time
import uuid

MAX_LOG_BYTES = 8 * 1024 * 1024
RETENTION_CHOICES = {'保留全部（不自动删除）': 0, '保留 7 天': 7, '保留 30 天': 30, '保留 90 天': 90}


def rotate_log(path, incoming=0):
    """Archive a growing log without deleting it, even with retention disabled."""
    path = Path(path)
    try:
        if path.is_file() and path.stat().st_size and path.stat().st_size + incoming > MAX_LOG_BYTES:
            destination = path.with_name(f'{path.stem}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}.log')
            path.rename(destination)
            return destination
    except OSError:
        pass
    return None


def discover_logs(global_logs, roots):
    candidates = set()
    global_logs = Path(global_logs).resolve()
    patterns = ('log/*.log', '*/log/*.log', 'logs/*.log', 'logs/*/*.log', 'results/*/*/log.txt', 'results/*/*/blender-logs/*.log',
                '*/logs/*.log', '*/logs/*/*.log', '*/results/*/*/log.txt', '*/results/*/*/blender-logs/*.log',
                'blender-logs/*.log', 'log.txt')
    for path in global_logs.glob('*.log'):
        if path.is_file() and not path.is_symlink() and path.resolve().parent == global_logs:
            candidates.add(path.resolve())
    for value in set(map(str, roots)):
        root = Path(value).resolve()
        if not root.is_dir():
            continue
        for pattern in patterns:
            for path in root.glob(pattern):
                if '.trash' in path.parts or path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve()
                if resolved.is_relative_to(root):
                    candidates.add(resolved)
    return sorted(candidates)


def expired_logs(global_logs, roots, days, now=None):
    if not isinstance(days, int) or days <= 0:
        return []
    cutoff = (time.time() if now is None else now) - days * 86400
    result = []
    for path in discover_logs(global_logs, roots):
        try:
            stat = path.stat()
            # Current append targets are excluded; old archives remain eligible.
            if path.parent == Path(global_logs).resolve() and path.name in ('session.log', 'launcher.log', 'startup.log'):
                continue
            if stat.st_mtime < cutoff:
                result.append({'path': str(path), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
        except OSError:
            pass
    return result


def delete_expired(candidates, days, now=None):
    cutoff = (time.time() if now is None else now) - days * 86400
    removed, size, skipped = 0, 0, 0
    if days <= 0:
        return {'removed': 0, 'bytes': 0, 'skipped': len(candidates)}
    for item in candidates:
        path = Path(item['path'])
        try:
            stat = path.stat()
            # Recheck after confirmation so newly updated files cannot be removed.
            if path.is_symlink() or stat.st_mtime_ns != item['mtime_ns'] or stat.st_mtime >= cutoff:
                skipped += 1
                continue
            path.unlink()
            removed += 1
            size += stat.st_size
        except OSError:
            skipped += 1
    return {'removed': removed, 'bytes': size, 'skipped': skipped}
