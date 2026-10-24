"""A single native background compositor for camera preview and final renders."""
import bpy
from pathlib import Path
import re
from blender_scene_display import scene_name


def compositor_tree(scene):
    if hasattr(scene, 'compositing_node_group'):
        return scene.compositing_node_group
    return scene.node_tree if scene.use_nodes else None


def sequence_offset(image):
    match = re.search(r'(\d+)$', Path(image.filepath).stem)
    return int(match.group(1)) - 1 if match else 0


def configure_background(scene, image, frame_start, frame_duration):
    """Composite a synchronized plate behind transparent rendered geometry.

    Camera background overlays are only viewport references. Drawing an overlay
    and a material preview world also gives two competing background paths.
    Use the compositor in camera view as well as in the final render instead.
    """
    if hasattr(scene, 'compositing_node_group'):
        tree = bpy.data.node_groups.new(scene_name('Tracked Background', '跟踪背景合成'), 'CompositorNodeTree')
        scene.compositing_node_group = tree
        tree.interface.new_socket(name='Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        output = tree.nodes.new('NodeGroupOutput')
    else:
        scene.use_nodes = True
        tree = scene.node_tree; tree.nodes.clear()
        output = tree.nodes.new('CompositorNodeComposite')
    nodes = tree.nodes
    source = nodes.new('CompositorNodeImage'); source.image = image
    source.label = scene_name('Background Sequence', '背景序列')
    user = source.image_user if hasattr(source, 'image_user') else source
    user.frame_start = frame_start
    user.frame_duration = frame_duration
    user.frame_offset = sequence_offset(image)
    user.use_auto_refresh = True
    user.use_cyclic = False
    scale = nodes.new('CompositorNodeScale')
    if hasattr(scale, 'space'):
        scale.space = 'RENDER_SIZE'; scale.frame_method = 'STRETCH'
    else:
        scale.inputs['Type'].default_value = 'Render Size'
        scale.inputs['Frame Type'].default_value = 'Stretch'
    foreground = nodes.new('CompositorNodeRLayers'); foreground.scene = scene
    over = nodes.new('CompositorNodeAlphaOver')
    if over.inputs.get('Background') is not None:
        back, front, factor = over.inputs['Background'], over.inputs['Foreground'], over.inputs['Factor']
    else:
        factor, back, front = over.inputs[0], over.inputs[1], over.inputs[2]
    factor.default_value = 1
    tree.links.new(source.outputs['Image'], scale.inputs['Image'])
    tree.links.new(scale.outputs['Image'], back)
    tree.links.new(foreground.outputs['Image'], front)
    tree.links.new(over.outputs['Image'], output.inputs['Image'])
    source.location = (-600, -120); scale.location = (-370, -120)
    foreground.location = (-370, 140); over.location = (-100, 60); output.location = (140, 60)
    scene.render.film_transparent = True
    scene.render.use_compositing = True
    # Apply only this compositor, avoiding any video editor output override.
    scene.render.use_sequencer = False
    # Tracking plates are sRGB photos; preserve their appearance in the composite.
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.view_settings.exposure = 0; scene.view_settings.gamma = 1
    if scene.camera:
        scene.camera.data.show_background_images = False
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.shading.use_compositor = 'CAMERA'
    scene['background_composite'] = 1
    return tree


def restore_background(scene):
    """Upgrade an unmodified old export in memory when opened through the app."""
    if 'source_model' not in scene or scene.get('background_composite') == 1 or not scene.camera:
        return False
    # Leave user-created compositor graphs intact.
    existing = compositor_tree(scene)
    if existing and len(existing.nodes): return False
    backgrounds = scene.camera.data.background_images
    background = next((b for b in backgrounds if b.image and b.image.source == 'SEQUENCE'), None)
    if background is None: return False
    user = background.image_user
    configure_background(scene, background.image, user.frame_start, user.frame_duration)
    return True
