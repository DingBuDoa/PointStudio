"""Load the supplied addon from the distribution, without installing it globally."""
from pathlib import Path
import sys


def enable_bundled_plugin():
    import bpy
    import addon_utils
    root = Path(__file__).resolve().parents[1]
    if not (root / 'photogrammetry_importer/__init__.py').is_file():
        raise RuntimeError('Bundled photogrammetry_importer folder is missing')
    loaded = sys.modules.get('photogrammetry_importer')
    if loaded and Path(loaded.__file__).resolve().parent != root / 'photogrammetry_importer':
        addon_utils.disable('photogrammetry_importer', default_set=False)
        for name in list(sys.modules):
            if name == 'photogrammetry_importer' or name.startswith('photogrammetry_importer.'):
                del sys.modules[name]
    sys.path.insert(0, str(root))
    addon_utils.enable('photogrammetry_importer', default_set=True, persistent=True)
    if not bpy.context.preferences.addons.get('photogrammetry_importer'):
        raise RuntimeError('Could not enable bundled Photogrammetry Importer')
    import photogrammetry_importer
    print('[PLUGIN] Bundled Photogrammetry Importer ' + str(photogrammetry_importer.bl_info['version']), flush=True)
    return photogrammetry_importer
