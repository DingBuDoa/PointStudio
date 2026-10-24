"""Accumulate the same least-squares constraints with bounded CUDA workspace."""
import torch
from fastmap.memory_policy import current_limit


@torch.no_grad()
def accumulate_normal_equations(A, group_idx, num_groups, chunk_size=None):
    """A is (N, 9) or (N, 2, 9); no full (N, 9, 9) temporary."""
    if chunk_size is None:
        chunk_size = current_limit('equation_chunk', A.device)
    if chunk_size < 1:
        raise ValueError('chunk_size must be positive')
    width = A.shape[-1]
    constraint = torch.zeros(num_groups, width, width, device=A.device, dtype=A.dtype)
    start = 0
    while start < A.shape[0]:
        stop = min(start + chunk_size, A.shape[0])
        block = A[start:stop]
        if block.ndim == 2:
            block = block.unsqueeze(-2)
        try:
            products = block.transpose(-1, -2) @ block
        except torch.cuda.OutOfMemoryError:
            if A.device.type != 'cuda' or chunk_size <= 1024:
                raise
            # Nothing has been accumulated for this block yet; retry it intact.
            del block
            chunk_size = max(1024, chunk_size // 2)
            torch.cuda.empty_cache()
            continue
        constraint.scatter_add_(0, group_idx[start:stop, None, None].expand(-1, width, width), products)
        del products, block
        start = stop
    return constraint
