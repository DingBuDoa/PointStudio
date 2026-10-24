"""Restore the addon's colored point display from a previously exported mesh."""
import bpy


def restore_colored_points(scene):
    from blender_scene_display import restore_native_points
    if restore_native_points(scene): return True
    if 'source_model' not in scene or not scene.get('draw_colored_points', True):
        return False
    handles = [o for o in scene.objects if 'particle_coords' in o and 'particle_colors' in o]
    created = False
    if not handles:
        mesh = next((o for o in scene.objects if o.type == 'MESH'
                     and o.get('point_count') and o.data.attributes.get('point_color')), None)
        if mesh is None:
            return False
        from photogrammetry_importer.opengl.utility import _draw_coords_with_color
        coords = [tuple(v.co) for v in mesh.data.vertices]
        colors = [tuple(c.color) for c in mesh.data.attributes['point_color'].data]
        if len(coords) != len(colors):
            raise ValueError('Point positions and colors have different lengths')
        handle = _draw_coords_with_color(coords, colors, 5, True,
            reconstruction_collection=mesh.users_collection[0],
            object_anchor_handle_name='OpenGL Point Cloud')
        handle.matrix_world = mesh.matrix_world.copy()
        created = True
    for obj in scene.objects:
        if obj.type == 'MESH' and obj.get('point_count'):
            obj.select_set(False)
            obj.hide_set(True)
            for modifier in obj.modifiers:
                if modifier.type == 'NODES':
                    modifier.show_viewport = False
                    modifier.show_render = True
    scene['draw_colored_points'] = True
    return created
