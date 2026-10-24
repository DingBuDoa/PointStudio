"""Launch a chosen Blender with the local addon available, then open the result."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_plugin import enable_bundled_plugin

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--blend', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if not args.blend.is_file():
        raise FileNotFoundError(args.blend)
    enable_bundled_plugin()
    import bpy
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()))
    from blender_point_display import restore_colored_points
    restore_colored_points(bpy.context.scene)
    from blender_background import restore_background
    restore_background(bpy.context.scene)
