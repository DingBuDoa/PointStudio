"""Windows FastMap adapter: bounded workers, CUDA preflight, complete exports."""
from pathlib import Path
import os
import json
import runpy
import sqlite3
import struct
import sys
import time

from paths import ROOT, FASTMAP
sys.path.insert(0, str(FASTMAP))
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '2')


def complete_observations(database, model_dir):
    """Restore original database keypoint indices in FastMap's images.bin."""
    import numpy as np
    from fastmap.io import read_images_binary, read_points3D_binary

    model = Path(model_dir)
    images = read_images_binary(str(model / 'images.bin'))
    points = read_points3D_binary(str(model / 'points3D.bin'))
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as db:
        by_name = {name: image_id for image_id, name in db.execute('SELECT image_id, name FROM images')}
        keypoints = {image_id: (rows, cols, data) for image_id, rows, cols, data in db.execute('SELECT image_id, rows, cols, data FROM keypoints')}
    observations = {}
    for image_id, image in images.items():
        original_id = by_name[image.name]
        rows, cols, blob = keypoints[original_id]
        xy = np.frombuffer(blob, dtype='<f4').reshape(rows, cols)[:, :2].copy()
        observations[image_id] = (xy, np.full(rows, -1, dtype=np.int64))
    count = 0
    for point_id, point in points.items():
        for image_id, keypoint_id in zip(point.image_ids, point.point2D_idxs):
            if int(image_id) not in observations:
                raise ValueError(f'Point {point_id} refers to an unregistered image {image_id}')
            xy, ids = observations[int(image_id)]
            if not 0 <= int(keypoint_id) < len(ids):
                raise ValueError(f'Invalid keypoint index: {image_id}/{keypoint_id}')
            previous = ids[int(keypoint_id)]
            if previous not in (-1, point_id):
                raise ValueError('Two 3D points share the same image keypoint')
            ids[int(keypoint_id)] = point_id
            count += 1
    temporary = model / 'images.complete.tmp'
    with temporary.open('wb') as stream:
        stream.write(struct.pack('<Q', len(images)))
        for image_id, image in sorted(images.items()):
            stream.write(struct.pack('<idddddddi', image_id, *image.qvec, *image.tvec, image.camera_id))
            stream.write(image.name.encode('utf-8') + bytes([0]))
            xy, ids = observations[image_id]
            stream.write(struct.pack('<Q', len(ids)))
            records = np.empty(len(ids), dtype=[('x', '<f8'), ('y', '<f8'), ('id', '<i8')])
            records['x'], records['y'], records['id'] = xy[:, 0], xy[:, 1], ids
            stream.write(records.tobytes())
    check = read_images_binary(str(temporary))
    assert sum(int((im.point3D_ids >= 0).sum()) for im in check.values()) == count
    os.replace(temporary, model / 'images.bin')
    print(f'[EXPORT] Restored {count} image observations for {len(images)} cameras.', flush=True)


def main():
    import argparse
    import torch
    import fastmap.color
    from fastmap.memory_policy import configure_cuda

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--database', required=True)
    parser.add_argument('--output_dir', required=True)
    args, _ = parser.parse_known_args()
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA-enabled PyTorch cannot access the GPU.')
    workers = max(1, min(2, int(os.environ.get('AUTOTRACKER_COLOR_WORKERS', '1'))))
    fastmap.color.cpu_count = lambda: workers + 1
    torch.set_num_threads(2)
    torch.manual_seed(42)
    print(f'[GPU] FastMap / {torch.cuda.get_device_name(0)} / PyTorch {torch.__version__}', flush=True)
    print('[GPU] Custom kernels are optional; PyTorch fallback still executes CUDA tensors.', flush=True)
    policy = configure_cuda(0)
    # Test an actual CUDA kernel, including GPU architecture support.
    assert (torch.ones(1, device='cuda:0') + 1).item() == 2
    print(f"[GPU] Memory policy: total {policy['total_bytes']/2**30:.2f} GiB, free {policy['free_bytes']/2**30:.2f} GiB; "
          f"up to {policy['match_pairs']} matches per image-pair batch (single pairs stay intact); "
          f"{policy['equation_chunk']} normal-equation / {policy['sparse_batch']} triangulation chunk. "
          'Live headroom may reduce these limits further; global scene tensors still need VRAM.', flush=True)
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    runpy.run_path(str(FASTMAP / 'run.py'), run_name='__main__')
    (Path(args.output_dir) / 'memory-policy.json').write_text(json.dumps(policy, indent=2), encoding='utf-8')
    complete_observations(args.database, Path(args.output_dir) / 'sparse/0')
    torch.cuda.synchronize()
    print(f'[GPU] FastMap finished in {time.perf_counter() - started:.2f}s; peak tensor allocation {torch.cuda.max_memory_allocated()/2**20:.1f} MiB.', flush=True)


if __name__ == '__main__':
    main()
