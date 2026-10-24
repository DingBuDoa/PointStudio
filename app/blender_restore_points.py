"""Create a repaired copy of an export, preserving its mesh and animation."""
import argparse
from pathlib import Path
import sys
import json
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy
from blender_plugin import enable_bundled_plugin
from blender_point_display import restore_colored_points

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if args.output.exists():
        raise FileExistsError(args.output)
    enable_bundled_plugin()
    bpy.ops.wm.open_mainfile(filepath=str(args.input.resolve()))
    bpy.context.scene['draw_colored_points'] = True
    restore_colored_points(bpy.context.scene)
    handles = [o for o in bpy.context.scene.objects if 'particle_coords' in o]
    if not handles:
        raise ValueError('No colored point data found')
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output.resolve()))
    old_report = args.input.with_suffix('.import.json')
    if old_report.is_file():
        report = json.loads(old_report.read_text(encoding='utf-8'))
        report.update(blend=str(args.output.resolve()), draw_points=True, repaired_from=str(args.input.resolve()))
        args.output.with_suffix('.import.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('COLORED_POINTS_RESTORED',len(handles[0]['particle_coords']),str(args.output),flush=True)
