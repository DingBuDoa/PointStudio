"""Conservative temporary-workspace caps, independent of GPU model names.

These are batching heuristics, not minimum-VRAM guarantees. Global scene
tensors and the native GLOMAP/cuDSS solver are outside this policy.
"""
GIB = 2**30
_policy = None


def choose_policy(total_bytes, free_bytes):
    if total_bytes <= 0 or free_bytes < 0:
        raise ValueError('Invalid GPU memory information')
    usable = min(total_bytes, free_bytes) * 0.8
    scale = min(1.0, usable / (8 * GIB))
    matches = 32768
    while matches < 262144 and matches * 2 <= 262144 * scale:
        matches *= 2
    return {'version': 'adaptive-memory-v1', 'total_bytes': int(total_bytes),
            'free_bytes': int(free_bytes), 'match_pairs': matches,
            'equation_chunk': min(65536, matches // 4),
            'sparse_batch': min(65536, matches // 4)}


def configure_cuda(device=0):
    import torch
    global _policy
    free, total = torch.cuda.mem_get_info(device)
    _policy = choose_policy(total, free)
    return dict(_policy)


def current_limit(kind, device):
    import torch
    device = torch.device(device)
    defaults = {'match_pairs': 262144, 'equation_chunk': 65536, 'sparse_batch': 65536}
    if device.type != 'cuda':
        return defaults[kind]
    policy = _policy if _policy is not None else configure_cuda(device)
    free, total = torch.cuda.mem_get_info(device)
    # Cached allocator blocks can be reused by the current process.
    reusable = max(0, torch.cuda.memory_reserved(device) - torch.cuda.memory_allocated(device))
    live = choose_policy(total, min(total, free + reusable))
    return min(policy[kind], live[kind])
