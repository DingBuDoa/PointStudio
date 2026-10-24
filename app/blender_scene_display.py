"""Portable point colors and ordinary lighting in Blender 4.x / 5.x files."""
import math
import bpy
import numpy as np


def scene_name(english, chinese):
    language = bpy.context.preferences.view.language
    locale = bpy.app.translations.locale if language == 'DEFAULT' else language
    return chinese if locale.lower().startswith('zh') else english


def configure_point_cloud(cloud, radius=.005, render_mesh=True):
    """Store a native display in the file, independent of Python draw handlers."""
    mesh = cloud.data
    colors = mesh.color_attributes.get('point_color')
    if colors is None or len(colors.data) != len(mesh.vertices):
        raise ValueError('The point cloud has no complete point_color attribute')
    # COLMAP RGB and the old exporter store sRGB; Blender FLOAT_COLOR is linear.
    if cloud.get('point_color_space') != 'LINEAR':
        values = np.empty(len(colors.data) * 4, dtype=np.float32)
        colors.data.foreach_get('color', values)
        values = values.reshape((-1, 4))
        rgb = np.clip(values[:, :3], 0, 1)
        values[:, :3] = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
        colors.data.foreach_set('color', values.ravel())
        cloud['point_color_space'] = 'LINEAR'
    mesh.color_attributes.active_color = colors

    material = bpy.data.materials.new(scene_name('Point Colors', '点云颜色'))
    material.use_nodes = True
    nodes = material.node_tree.nodes; nodes.clear()
    attribute = nodes.new('ShaderNodeAttribute'); attribute.attribute_name = 'point_color'
    emission = nodes.new('ShaderNodeEmission'); emission.inputs['Strength'].default_value = 1
    output = nodes.new('ShaderNodeOutputMaterial')
    material.node_tree.links.new(attribute.outputs['Color'], emission.inputs['Color'])
    material.node_tree.links.new(emission.outputs[0], output.inputs['Surface'])
    mesh.materials.clear(); mesh.materials.append(material)

    # Assign the material after realization so every sphere reads its own point's color.
    group = bpy.data.node_groups.new(scene_name('Colored Point Display', '彩色点云显示'), 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    radius_input = group.interface.new_socket(name=scene_name('Point Radius', '点半径'), in_out='INPUT', socket_type='NodeSocketFloat')
    radius_input.default_value = radius; radius_input.min_value = .0001; radius_input.max_value = 100
    nodes = group.nodes
    source = nodes.new('NodeGroupInput'); output = nodes.new('NodeGroupOutput')
    points = nodes.new('GeometryNodeMeshToPoints'); points.mode = 'VERTICES'
    sphere = nodes.new('GeometryNodeMeshIcoSphere')
    sphere.inputs['Radius'].default_value = radius
    group.links.new(source.outputs[radius_input.name], sphere.inputs['Radius'])
    sphere.inputs['Subdivisions'].default_value = 1
    instances = nodes.new('GeometryNodeInstanceOnPoints')
    realize = nodes.new('GeometryNodeRealizeInstances')
    assign = nodes.new('GeometryNodeSetMaterial'); assign.inputs['Material'].default_value = material
    for first, first_socket, second, second_socket in (
        (source, 'Geometry', points, 'Mesh'), (points, 'Points', instances, 'Points'),
        (sphere, 'Mesh', instances, 'Instance'), (instances, 'Instances', realize, 'Geometry'),
        (realize, 'Geometry', assign, 'Geometry'), (assign, 'Geometry', output, 'Geometry')):
        group.links.new(first.outputs[first_socket], second.inputs[second_socket])
    for modifier in list(cloud.modifiers):
        if modifier.type == 'NODES': cloud.modifiers.remove(modifier)
    modifier = cloud.modifiers.new(scene_name('Colored Points', '彩色点云'), 'NODES')
    modifier.node_group = group
    modifier.show_viewport = True; modifier.show_render = render_mesh
    cloud.hide_set(False); cloud.hide_viewport = False; cloud.hide_render = not render_mesh
    # Tracking markers are a reference; they should not shadow subsequently added objects.
    cloud.visible_shadow = False
    cloud.select_set(False)
    cloud['point_radius'] = radius
    return modifier


def add_point_handle(cloud, radius=.005):
    """Keep the original point-cloud handle and data alongside the editable mesh."""
    handle = bpy.data.objects.new(scene_name('Point Cloud', '点云（Point Cloud）'), None)
    cloud.users_collection[0].objects.link(handle)
    handle.empty_display_type = 'PLAIN_AXES'
    handle.empty_display_size = radius
    handle.matrix_world = cloud.matrix_world.copy()
    handle['particle_coords'] = [tuple(v.co) for v in cloud.data.vertices]
    colors = np.empty(len(cloud.data.vertices) * 4, dtype=np.float32)
    cloud.data.color_attributes['point_color'].data.foreach_get('color', colors)
    rgb = np.clip(colors.reshape((-1, 4))[:, :3], 0, 1)
    rgb = np.where(rgb <= .0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - .055)
    handle['particle_colors'] = np.column_stack((rgb, np.ones(len(rgb)))).tolist()
    handle['point_size'] = 2
    handle['native_display_mesh'] = cloud
    cloud['point_cloud_handle'] = handle
    transform = cloud.matrix_world.copy()
    cloud.parent = handle
    cloud.matrix_world = transform
    handle.hide_render = True
    # Native geometry supplies the display, avoiding overlapping GPU dots.
    return handle


def configure_viewports(scene):
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != 'VIEW_3D': continue
            space = area.spaces.active
            space.region_3d.view_perspective = 'CAMERA'
            space.shading.type = 'MATERIAL'
            space.shading.color_type = 'MATERIAL'
            space.shading.use_scene_lights = True
            space.shading.use_scene_world = True
            space.clip_end = 100000


def configure_lighting(scene):
    world = bpy.data.worlds.new(scene_name('World', '世界环境'))
    world.use_nodes = True
    background = next(node for node in world.node_tree.nodes if node.type == 'BACKGROUND')
    background.inputs['Color'].default_value = (.08, .08, .08, 1)
    background.inputs['Strength'].default_value = .5
    scene.world = world
    lighting = bpy.data.collections.new(scene_name('Preview Lighting', '预览灯光'))
    scene.collection.children.link(lighting)
    light_data = bpy.data.lights.new(scene_name('Preview Sun', '预览太阳光'), 'SUN')
    light_data.energy = 2; light_data.angle = math.radians(5)
    light = bpy.data.objects.new(light_data.name, light_data); lighting.objects.link(light)
    light.rotation_euler = tuple(math.radians(v) for v in (25, -25, -35))
    working = bpy.data.collections.new(scene_name('Scene Objects', '场景物体'))
    scene.collection.children.link(working)
    bpy.context.view_layer.active_layer_collection = bpy.context.view_layer.layer_collection.children[working.name]
    configure_viewports(scene)


def restore_native_points(scene):
    if 'source_model' not in scene: return False
    clouds = [o for o in scene.objects if o.type == 'MESH' and o.get('point_count') and o.data.color_attributes.get('point_color')]
    if not clouds: return False
    if scene.get('native_point_display') != 1:
        for cloud in clouds:
            configure_point_cloud(cloud, cloud.get('point_radius', .05), scene.get('points_as_mesh', True))
        for obj in scene.objects:
            if 'particle_coords' in obj: obj.hide_set(True)
        scene['native_point_display'] = 1
        configure_viewports(scene)
    return True
