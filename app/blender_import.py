"""Run with Blender --background --python this_file -- --model ... --output ..."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bpy
from mathutils import Matrix, Quaternion, Vector
from blender_plugin import enable_bundled_plugin
from blender_scene_display import configure_point_cloud, configure_lighting, scene_name, add_point_handle
from blender_background import configure_background, sequence_offset


def progress(percent, text):
    print('[BLENDER_PROGRESS] ' + json.dumps(dict(percent=percent, text=text), ensure_ascii=False), flush=True)


def read_model(model):
    cameras = {}
    for line in (model / 'cameras.txt').read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        fields = line.split()
        cameras[int(fields[0])] = (fields[1], int(fields[2]), int(fields[3]), list(map(float, fields[4:])))
    images = []
    with (model / 'images.txt').open(encoding='utf-8') as stream:
        while True:
            line = stream.readline()
            if not line:
                break
            if line.startswith('#') or not line.strip():
                continue
            fields = line.split(maxsplit=9)
            images.append({'id': int(fields[0]), 'q': list(map(float, fields[1:5])),
                           't': list(map(float, fields[5:8])), 'camera': int(fields[8]), 'name': fields[9].strip()})
            stream.readline()  # The second line contains the image's 2D observations.
    return cameras, images


def intrinsics(camera):
    kind, width, height, parameters = camera
    if kind in ('SIMPLE_PINHOLE', 'SIMPLE_RADIAL', 'RADIAL', 'SIMPLE_RADIAL_FISHEYE', 'RADIAL_FISHEYE'):
        f, cx, cy = parameters[:3]
        return width, height, f, f, cx, cy
    if kind in ('PINHOLE', 'OPENCV', 'OPENCV_FISHEYE', 'FULL_OPENCV', 'FOV', 'THIN_PRISM_FISHEYE'):
        fx, fy, cx, cy = parameters[:4]
        return width, height, fx, fy, cx, cy
    raise ValueError('Unsupported camera model: ' + kind)


def import_model(model, output, fps, as_mesh=True, suppress_warnings=True,
                 background_images=True, point_radius=0.005, draw_points=True,
                 images_directory=None, report_path=None):
    if not as_mesh and not draw_points:
        raise ValueError('Enable colored point display or mesh export')
    progress(2, '正在加载摄影测量导入组件…')
    plugin = enable_bundled_plugin()
    progress(5, '正在读取相机数据…')
    cameras, images = read_model(model)
    if not images:
        raise ValueError('Model has no registered cameras')
    # Preserve this Blender installation's preferences, but build a fresh scene.
    old_scenes = list(bpy.data.scenes)
    scene = bpy.data.scenes.new(scene_name('Camera Reconstruction', '相机重建'))
    bpy.context.window.scene = scene
    for old_scene in old_scenes: bpy.data.scenes.remove(old_scene)
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    images_directory = images_directory or next((candidate for parent in model.parents
        for candidate in (parent / '_cache/images', parent / 'images') if candidate.is_dir()), None)
    if background_images and images_directory is None:
        raise ValueError('Missing images folder for camera background. Disable background images or restore the project images folder.')
    existing_collections = set(bpy.data.collections)
    # Call the supplied addon's actual COLMAP operator. Its misspelled property
    # add_points_as_mesh_oject is part of its public RNA interface.
    progress(10, '正在读取点云及颜色…')
    imported = bpy.ops.import_scene.colmap_model('EXEC_DEFAULT', directory=str(model) + '/',
        image_dp=str(images_directory or model), image_fp_type='RELATIVE',
        use_workspace_images=False, import_cameras=False, add_camera_motion_as_animation=False,
        add_background_images=False, add_animated_camera_background_images=False,
        import_points=True, draw_points_with_gpu=False,
        add_points_to_point_cloud_handle=False, add_points_as_mesh_oject=True,
        add_mesh_to_point_geometry_nodes=False, point_radius=point_radius,
        point_subdivisions=1, add_color_as_custom_property=False,
        center_points=False, point_cloud_display_sparsity=1,
        suppress_distortion_warnings=suppress_warnings, adjust_clipping_distance=False)
    if imported != {'FINISHED'}:
        raise RuntimeError('Photogrammetry Importer did not finish')
    collection = next((c for c in bpy.data.collections if c not in existing_collections), None)
    if collection is None:
        raise RuntimeError('Photogrammetry Importer created no reconstruction')
    collection.name = scene_name('AutoTracker Reconstruction', '相机与点云重建')
    cloud = next(obj for obj in collection.all_objects if obj.type == 'MESH')
    count = len(cloud.data.vertices)
    points = [tuple(cloud.data.vertices[i].co) for i in range(0, count, max(1, count // 25))]
    if not count:
        raise ValueError('Model has no points')
    cloud['point_count'] = count
    cloud['description'] = 'Imported with bundled Photogrammetry Importer'
    cloud.name = scene_name('Mesh Point Cloud', '网格点云（Mesh Point Cloud）')
    progress(25, f'已读取 {count:,} 个点，正在生成相机动画…')

    camera_data = bpy.data.cameras.new(scene_name('Tracked Camera', '跟踪相机'))
    camera = bpy.data.objects.new(camera_data.name, camera_data)
    collection.objects.link(camera)
    scene.camera = camera
    camera.rotation_mode = 'QUATERNION'
    camera_data.sensor_fit = 'HORIZONTAL'
    camera_data.sensor_width = 36
    camera_data.clip_start = 0.001
    camera_data.clip_end = 100000
    camera_data.display_size = 0.25
    conversion = Matrix.Diagonal((1, -1, -1, 1))
    previous = None
    frames = []
    projection_errors = []
    ordered_images = sorted(images, key=lambda item: item['name'])
    frame_numbers = []
    for sequence, image in enumerate(ordered_images, 1):
        match = re.search(r'(\d+)$', Path(image['name']).stem)
        frame_numbers.append(int(match.group(1)) if match else sequence)
    if len(set(frame_numbers)) != len(frame_numbers):
        raise ValueError('Image names produce duplicate animation frame numbers')
    if background_images:
        first = ordered_images[0]
        image_path = images_directory / first['name']
        if not image_path.is_file():
            raise FileNotFoundError(f'Missing camera background: {image_path}')
        background = camera_data.background_images.new()
        background.image = bpy.data.images.load(str(image_path), check_existing=True)
        background.image.source = 'SEQUENCE'
        background.image_user.frame_start = frame_numbers[0]
        background.image_user.frame_offset = sequence_offset(background.image)
        background.image_user.frame_duration = max(frame_numbers) - min(frame_numbers) + 1
        background.image_user.use_auto_refresh = True
        background.display_depth = 'BACK'
        background.alpha = 0.7
        camera_data.show_background_images = True
    from bpy_extras.object_utils import world_to_camera_view
    for sequence, image in enumerate(ordered_images, 1):
        frame = frame_numbers[sequence - 1]
        frames.append(frame)
        width, height, fx, fy, cx, cy = intrinsics(cameras[image['camera']])
        scene.render.resolution_x, scene.render.resolution_y = width, height
        scene.render.resolution_percentage = 100
        scene.render.pixel_aspect_x = 1
        scene.render.pixel_aspect_y = fx / fy
        pose = Quaternion(image['q']).to_matrix().to_4x4()
        pose.translation = Vector(image['t'])
        camera.matrix_world = pose.inverted() @ conversion
        quaternion = camera.rotation_quaternion.copy()
        if previous is not None and previous.dot(quaternion) < 0:
            quaternion.negate()
            camera.rotation_quaternion = quaternion
        previous = quaternion
        camera_data.lens = fx * 36 / width
        camera_data.shift_x = (width / 2 - cx) / width
        camera_data.shift_y = (cy - height / 2) * (fx / fy) / width
        camera.keyframe_insert(data_path='location', frame=frame)
        camera.keyframe_insert(data_path='rotation_quaternion', frame=frame)
        for property_name in ('lens', 'shift_x', 'shift_y'):
            camera_data.keyframe_insert(data_path=property_name, frame=frame)
        # Check axis conversion and intrinsics against the ideal COLMAP projection.
        if sequence in (1, len(images) // 2, len(images)):
            bpy.context.view_layer.update()
            for point in points:
                projected = pose @ Vector(point)
                if projected.z <= 0:
                    continue
                uv = world_to_camera_view(scene, camera, Vector(point))
                expected = (fx * projected.x / projected.z + cx, fy * projected.y / projected.z + cy)
                error = max(abs(uv.x * width - expected[0]), abs((1 - uv.y) * height - expected[1]))
                projection_errors.append(error)
        if sequence == 1 or sequence % max(1, len(images) // 20) == 0 or sequence == len(images):
            progress(25 + int(50 * sequence / len(images)), f'正在生成相机动画：{sequence}/{len(images)} 帧')

    # Blender 4.4+ uses layered actions; older versions expose legacy fcurves.
    for owner in (camera, camera_data):
        action = owner.animation_data.action
        if hasattr(action, 'fcurves'):
            curves = action.fcurves
        else:
            curves = [curve for layer in action.layers for strip in layer.strips
                      for bag in strip.channelbags for curve in bag.fcurves]
        for curve in curves:
            for key in curve.keyframe_points:
                key.interpolation = 'LINEAR'
    scene.render.fps = max(1, round(fps))
    scene.render.fps_base = scene.render.fps / fps
    scene.frame_start, scene.frame_end = min(frames), max(frames)
    scene.frame_set(scene.frame_start)
    scene['source_model'] = str(model)
    scene['camera_parameters'] = json.dumps(cameras)
    scene['registered_frames'] = len(images)
    scene['photogrammetry_importer_version'] = str(plugin.bl_info['version'])
    scene['points_as_mesh'] = as_mesh
    scene['suppress_distortion_warnings'] = suppress_warnings
    scene['lens_distortion_note'] = 'Camera animation uses ideal pinhole projection; lens distortion is not applied. COLMAP parameters are stored on scene.'
    scene['scale_note'] = 'Monocular SfM has arbitrary global scale and orientation.'
    maximum_error = max(projection_errors, default=0)
    if maximum_error > 0.1:
        raise ValueError(f'Blender/COLMAP ideal projection mismatch: {maximum_error:.6f}px')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    # Build the expensive display graph only after camera keyframes are complete.
    progress(80, '正在保存两种点云对象、颜色与显示设置…')
    configure_point_cloud(cloud, point_radius, render_mesh=as_mesh)
    if draw_points: add_point_handle(cloud, point_radius)
    configure_lighting(scene)
    if background_images:
        progress(87, '正在配置预览与渲染背景合成…')
        configure_background(scene, background.image, frame_numbers[0], max(frame_numbers) - min(frame_numbers) + 1)
    scene['native_point_display'] = 1
    scene['export_language'] = bpy.context.preferences.view.language
    scene['draw_colored_points'] = draw_points
    progress(92, '正在写入 Blender 工程文件…')
    bpy.ops.wm.save_as_mainfile(filepath=str(output))
    report = {'blend': str(output), 'registered_frames': len(images), 'points': count,
              'frame_start': scene.frame_start, 'frame_end': scene.frame_end, 'fps': fps,
              'max_ideal_projection_difference_px': maximum_error,
              'lens_distortion_applied': False, 'as_mesh': as_mesh,
              'suppress_distortion_warnings': suppress_warnings,
              'background_images': background_images, 'point_radius': point_radius,
              'render_background': background_images,
              'draw_points': draw_points,
              'native_point_display': True, 'viewport_shading': 'MATERIAL',
              'language': bpy.context.preferences.view.language,
              'importer': 'bundled photogrammetry_importer', 'importer_version': list(plugin.bl_info['version']),
              'blender_version': bpy.app.version_string, 'blender_binary': bpy.app.binary_path}
    (report_path or output.with_suffix('.import.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('[BLENDER_IMPORT_PASS] ' + json.dumps(report), flush=True)
    progress(99, '工程已保存，正在结束 Blender 后台进程…')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fps', type=float, default=30)
    parser.add_argument('--as-mesh', type=int, choices=(0, 1), default=1)
    parser.add_argument('--suppress-distortion-warnings', type=int, choices=(0, 1), default=1)
    parser.add_argument('--background-images', type=int, choices=(0, 1), default=1)
    parser.add_argument('--point-radius', type=float, default=0.005)
    parser.add_argument('--draw-points', type=int, choices=(0, 1), default=1)
    parser.add_argument('--images', type=Path)
    parser.add_argument('--report', type=Path)
    arguments = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if not 0 < arguments.fps <= 240 or not 0 < arguments.point_radius <= 100:
        raise ValueError('FPS or point radius is outside the allowed range')
    import_model(arguments.model.resolve(), arguments.output.resolve(), arguments.fps,
                 bool(arguments.as_mesh), bool(arguments.suppress_distortion_warnings),
                 bool(arguments.background_images), arguments.point_radius, bool(arguments.draw_points),
                 arguments.images, arguments.report)
