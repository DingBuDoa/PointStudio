"""Focused regression tests for real FFmpeg output and Qt/queue integration."""
from dataclasses import asdict, replace
from pathlib import Path
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from paths import runtime_environment, BINARY_DIRS
os.environ.update(runtime_environment())
from tracker_core import Options, Pipeline, Cancelled, video_filters
from task_queue import TaskQueue
from workload import source_signature, video_scene, video_info
from hardware import settings_signature
from project_library import ProjectLibrary
from project_layout import create_project, safe_name, blend_filename
from qt_app import MainWindow, configure_application
from qt_dialogs import TaskDialog, BlenderDialog
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import QTimer, Qt, QPoint
from PySide6.QtTest import QTest


def wait_until(predicate, timeout=8, app=None):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline: raise AssertionError('operation timed out')
        if app: app.processEvents()
        time.sleep(.01)


class QueueTests(unittest.TestCase):
    def test_glomap_fallback_is_live_deduplicated_and_queue_continues(self):
        events = []; live = []
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests/artifacts') as directory:
            class SolverPipeline(Pipeline):
                def run(self, path, existing_scene=False):
                    self.log_dir = Path(directory)
                    fallback = Path(path).stem == 'fallback'
                    lines = '[GPU] Global positioning: CUDA_SPARSE\n[GPU] Bundle adjustment: CUDA_SPARSE'
                    if fallback: lines += '\nFalling back to CPU\nFALLING BACK to CPU'
                    script = f'import time; print({lines!r}, flush=True); time.sleep(.3)'
                    self.command([sys.executable, '-u', '-c', script], '04-glomap')
                    return {'gpu_solver_active': self.verify_glomap_gpu(), 'status': 'complete',
                            'solver_warnings': list(self.warnings), 'project_dir': directory,
                            'model': str(Path(directory) / 'point could')}
            def receive(event):
                events.append(event)
                if event['type'] == 'solver_warning':
                    live.append(tasks.pipeline.process.poll() is None)
            tasks = TaskQueue(receive, SolverPipeline, lambda result: None)
            first = tasks.add('fallback.mp4', Options(backend='glomap'))
            second = tasks.add('gpu.mp4', Options(backend='glomap'))
            tasks.jobs[first]['blender'] = {'version': 'Blender 5.2', 'path': 'unused.exe'}
            def export(pipeline, result, output, settings):
                self.assertFalse(result['gpu_solver_active'])
                self.assertEqual(result['solver_warnings'][0]['code'], 'cpu_fallback')
                output.write_bytes(b'BLENDER-test')
                return str(output)
            with patch('blender_export.export_blender', side_effect=export) as exported:
                tasks.start(); wait_until(lambda: not tasks.running)
                exported.assert_called_once()
            self.assertEqual(live, [True])
            self.assertEqual(tasks.jobs[first]['state'], '完成')
            self.assertFalse(tasks.jobs[first]['result']['gpu_solver_active'])
            self.assertTrue((Path(directory) / 'fallback_5.2.blend').is_file())
            self.assertEqual(tasks.jobs[first]['solver_warnings'][0]['code'], 'cpu_fallback')
            self.assertEqual(tasks.jobs[second]['state'], '完成')
            self.assertTrue(tasks.jobs[second]['result']['gpu_solver_active'])
            self.assertFalse(tasks.jobs[second]['solver_warnings'])
            self.assertEqual(len([e for e in events if e['type'] == 'solver_warning']), 1)

    def test_glomap_partial_gpu_and_explicit_cpu(self):
        events = []
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests/artifacts') as directory:
            pipeline = Pipeline(Options(backend='glomap'), events.append); pipeline.log_dir = Path(directory)
            log = pipeline.log_dir / '04-glomap.log'
            log.write_text('[GPU] Global positioning: CUDA_SPARSE\n', encoding='utf-8')
            self.assertFalse(pipeline.verify_glomap_gpu())
            self.assertFalse(pipeline.verify_glomap_gpu())
            self.assertEqual(len(pipeline.warnings), 1)
            self.assertEqual(pipeline.warnings[0]['code'], 'gpu_incomplete')
            self.assertIn('50', pipeline.warnings[0]['text'])
            self.assertIn('求解提醒', (pipeline.log_dir / 'task.log').read_text(encoding='utf-8'))
            for options, label in ((Options(backend='glomap', glomap_gpu=False), '04-glomap'),
                                   (Options(), '04-fastmap'), (Options(backend='glomap'), '02-features')):
                other = Pipeline(options, events.append); other.log_dir = Path(directory)
                other.command([sys.executable, '-c', 'print("Falling back to CPU")'], label)
                self.assertFalse(other.warnings)

    def test_priority_moves_groups_and_starts_subset_in_queue_order(self):
        calls = []
        class FakePipeline:
            def __init__(self, options, callback): pass
            def run(self, path, existing_scene=False):
                calls.append(Path(path).stem)
                return {'status': 'complete'}
        tasks = TaskQueue(lambda event: None, FakePipeline, lambda result: None)
        a, b, c, d = [tasks.add(f'{name}.mp4', Options()) for name in ('a', 'b', 'c', 'd')]
        self.assertFalse(tasks.move([a], -1))
        self.assertFalse(tasks.move([d], 1))
        self.assertTrue(tasks.move([b, c], 1))
        self.assertEqual(list(tasks.snapshot()), [a, d, b, c])
        self.assertTrue(tasks.move([b, c], -1))
        self.assertEqual(list(tasks.snapshot()), [a, b, c, d])
        self.assertTrue(tasks.move([c], -1))
        self.assertTrue(tasks.start([d, c, a]))
        wait_until(lambda: not tasks.running)
        self.assertEqual(calls, ['a', 'c', 'd'])
        self.assertEqual(tasks.jobs[b]['state'], '等待')
        self.assertFalse(tasks.can_move([a], 1))

    def test_live_priority_pause_new_tasks_and_failure_continue_serially(self):
        entered = threading.Event(); release = threading.Event(); calls = []
        class FakePipeline:
            def __init__(self, options, callback): pass
            def run(self, path, existing_scene=False):
                name = Path(path).stem; calls.append(name)
                if name == 'a': entered.set(); release.wait(5)
                if name == 'b': raise RuntimeError('expected failure')
                return {'status': 'complete'}
        events = []
        tasks = TaskQueue(events.append, FakePipeline, lambda result: None)
        a, b, c, d = [tasks.add(f'{name}.mp4', Options()) for name in ('a', 'b', 'c', 'd')]
        tasks.start(); self.assertTrue(entered.wait(3))
        self.assertFalse(tasks.move([a], 1))
        self.assertTrue(tasks.move([c], -1))
        e = tasks.add('e.mp4', Options())
        self.assertEqual(tasks.jobs[e]['state'], '排队')
        self.assertTrue(tasks.move([e], -1)); self.assertTrue(tasks.move([e], -1))
        self.assertEqual(calls, ['a'])
        tasks.pause(True); release.set()
        wait_until(lambda: tasks.jobs[a]['state'] == '完成')
        self.assertEqual(calls, ['a'])
        tasks.pause(False); wait_until(lambda: not tasks.running)
        self.assertEqual(calls, ['a', 'c', 'e', 'b', 'd'])
        self.assertEqual(tasks.jobs[b]['state'], '失败')
        self.assertEqual(tasks.jobs[d]['state'], '完成')
        self.assertIn('4 项成功 · 1 项失败', events[-1]['text'])

    def test_actual_child_process_is_stopped(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests/artifacts') as directory:
            class ProcessPipeline(Pipeline):
                def run(self, path, existing_scene=False):
                    self.log_dir = Path(directory)
                    self.command([sys.executable, '-u', '-c', 'import time; print("ready", flush=True); time.sleep(60)'], 'cancel-check')
                    return {}
            tasks = TaskQueue(lambda event: None, ProcessPipeline, lambda result: None)
            first = tasks.add('one.mp4', Options()); second = tasks.add('two.mp4', Options())
            tasks.start()
            wait_until(lambda: tasks.pipeline is not None and tasks.pipeline.process is not None)
            process = tasks.pipeline.process
            tasks.cancel(); wait_until(lambda: not tasks.running)
            self.assertIsNotNone(process.poll())
            self.assertEqual(tasks.jobs[first]['state'], '已停止')
            self.assertEqual(tasks.jobs[second]['state'], '等待')

    def test_auto_blender_failure_keeps_reconstruction_result(self):
        result = {'model': str(ROOT / 'tests/artifacts/model/sparse/0'), 'status': 'complete'}
        class FakePipeline:
            def __init__(self, options, callback): pass
            def run(self, path, existing_scene=False): return result
        tasks = TaskQueue(lambda event: None, FakePipeline, lambda result: None)
        iid = tasks.add('video.mp4', Options())
        tasks.jobs[iid]['blender'] = {'version': 'Blender 4.2.15', 'path': 'blender.exe'}
        with patch('blender_export.export_blender', side_effect=RuntimeError('export test failure')):
            tasks.start(); wait_until(lambda: not tasks.running)
        self.assertEqual(tasks.jobs[iid]['state'], '完成')
        self.assertEqual(tasks.jobs[iid]['result'], result)
        self.assertIn('export test failure', tasks.jobs[iid]['export_error'])

    def test_edit_wait_and_independent_options(self):
        events = queue.Queue(); first_started = threading.Event(); release = threading.Event(); calls = []
        class FakePipeline:
            def __init__(self, options, callback): self.options, self.callback = options, callback
            def run(self, path, existing_scene=False):
                calls.append((path, self.options.fps))
                if len(calls) == 1: first_started.set(); release.wait(5)
                return {'status': 'complete'}
        tasks = TaskQueue(events.put, FakePipeline, lambda result: None)
        first = tasks.add('one.mp4', Options(fps=10))
        second = tasks.add('two.mp4', Options(fps=20))
        self.assertTrue(tasks.start()); self.assertTrue(first_started.wait(3))
        self.assertIsNone(tasks.begin_edit(first))
        self.assertIsNotNone(tasks.begin_edit(second))
        release.set()
        wait_until(lambda: tasks.jobs[first]['state'] == '完成')
        self.assertEqual(len(calls), 1)
        options = dict(tasks.jobs[second]['options'], fps=12)
        tasks.end_edit({'options': options})
        wait_until(lambda: not tasks.running)
        self.assertEqual([fps for _, fps in calls], [10, 12])

    def test_cancel_waiting_queue_and_recover_pending(self):
        entered = threading.Event(); release = threading.Event()
        class FakePipeline:
            def __init__(self, options, callback): pass
            def run(self, path, existing_scene=False):
                entered.set(); release.wait(3)
                if self.cancelled.is_set(): raise Cancelled('stopped')
                return {}
            def cancel(self): self.cancelled.set(); release.set()
        tasks = TaskQueue(lambda event: None, FakePipeline, lambda result: None)
        first = tasks.add('one.mp4', Options()); second = tasks.add('two.mp4', Options())
        tasks.start(); self.assertTrue(entered.wait(3)); self.assertFalse(tasks.remove(first))
        tasks.pause(True); tasks.cancel(); wait_until(lambda: not tasks.running)
        self.assertEqual(tasks.jobs[first]['state'], '已停止')
        self.assertEqual(tasks.jobs[second]['state'], '等待')


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([]); configure_application(cls.app)
        cls.artifacts = ROOT / 'tests/artifacts'; cls.artifacts.mkdir(exist_ok=True)
        cls.video = cls.artifacts / 'test-24fps.mp4'
        subprocess.run([str(BINARY_DIRS['03 FFMPEG'] / 'ffmpeg.exe'), '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.video)], check=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.artifacts)
        self.window = MainWindow(config_dir=self.temp.name, background=False); self.window.show(); self.app.processEvents()

    def tearDown(self):
        self.window.poller.stop(); self.window.hide(); self.window.deleteLater(); self.app.processEvents(); self.temp.cleanup()

    def test_solver_notice_survives_finished_and_persists_without_modal(self):
        window = self.window
        directory = Path(self.temp.name)
        model = directory / 'point could'; model.mkdir()
        for name in ('cameras.txt', 'images.txt', 'points3D.txt'):
            (model / name).write_text('1 2 3\n', encoding='utf-8')
        class FallbackPipeline(Pipeline):
            def run(self, path, existing_scene=False):
                self.log_dir = directory
                self.observe_glomap_output('Falling back to CPU')
                (directory / '04-glomap.log').write_text('Falling back to CPU\n', encoding='utf-8')
                return dict(status='complete', model=str(model), project_dir=str(directory), source=path,
                            metrics={'points': 1}, gpu_solver_active=self.verify_glomap_gpu(),
                            solver_warnings=list(self.warnings))
        window.tasks.pipeline_factory = FallbackPipeline
        window.tasks.sample_writer = lambda result: None
        iid = window.tasks.add(self.video, Options(backend='glomap'), '回退提示验证')
        with patch.object(window, 'refresh_projects'), patch.object(window, 'schedule_estimate'):
            window.start_queue()
            wait_until(lambda: not window.tasks.running, app=self.app)
            wait_until(lambda: window.events.empty(), app=self.app)
        self.assertTrue(window.solver_notice.isVisible())
        self.assertIn('回退到 CPU', window.solver_notice_text.text())
        self.assertIn('继续求解和导出', window.solver_notice_text.text())
        self.assertEqual(window.solver_notice_log, str(directory / '04-glomap.log'))
        self.assertIsNone(QApplication.activeModalWidget())
        window.table.selectRow(0); self.app.processEvents()
        self.assertIn('回退到 CPU', window.detail_info.text())
        self.assertEqual(window.selected()['state'], '完成')
        window.pages.setCurrentIndex(1); self.app.processEvents()
        self.assertTrue(window.solver_notice.isVisible())
        window.solver_notice.hide()
        saved = json.loads((directory / 'task-queue.json').read_text(encoding='utf-8'))
        self.assertEqual(saved[0]['solver_warnings'][0]['code'], 'cpu_fallback')
        window.tasks.jobs.clear(); window.restore_tasks(); window.rebuild_rows()
        window.pages.setCurrentIndex(0); window.table.selectRow(0); self.app.processEvents()
        self.assertEqual(window.tasks.jobs[iid]['state'], '完成')
        self.assertFalse(window.tasks.jobs[iid]['result']['gpu_solver_active'])
        self.assertIn('回退到 CPU', window.detail_info.text())
        self.assertIn('PointStudio', window.windowTitle())

    def test_blender_export_progress_responsive_completion_and_cancel(self):
        window = self.window; project = create_project(self.temp.name, '导出响应验证')
        model = project / 'point could'; model.mkdir()
        for name in ('cameras.txt', 'images.txt', 'points3D.txt'):
            (model / name).write_text('1 2 3\n', encoding='utf-8')
        result = dict(layout_version=2, project_dir=str(project), model=str(model), status='complete',
                      metrics={'points': 1}, source=str(self.video), log_dir=str(project / 'log'))
        iid = window.tasks.add(self.video, Options(), '导出响应验证')
        window.tasks.jobs[iid].update(state='完成', result=result)
        window.rebuild_rows(); window.table.selectRow(0); self.app.processEvents()
        settings = dict(path='unused-blender.exe', version='Blender 5.2', fps=24, as_mesh=True,
                        draw_points=True, suppress_warnings=True, background_images=False, point_radius=.005)
        ticks = []; timer = QTimer(); timer.setInterval(10); timer.timeout.connect(lambda: ticks.append(time.monotonic()))

        def real_child(pipeline, args, label):
            output = args[args.index('--output') + 1]
            script = ('import json,time\nfrom pathlib import Path\n'
                      'for percent in (20,70):\n'
                      ' print("[BLENDER_PROGRESS] " + json.dumps(dict(percent=percent,text="phase-"+str(percent))),flush=True)\n'
                      ' time.sleep(.35)\n'
                      f'Path({str(output)!r}).write_bytes(b"BLENDER-test")\n')
            return pipeline._command([sys.executable, '-u', '-c', script], label)

        with patch.object(window, 'choose_blender', return_value=settings), \
             patch.object(window, 'refresh_projects'), patch('tracker_core.Pipeline.command', new=real_child):
            for cancelled in (False, True):
                output = project / ('cancelled.blend' if cancelled else 'complete.blend')
                with patch('qt_app.QFileDialog.getSaveFileName', return_value=(str(output), '')):
                    started = time.monotonic(); window.export_selected()
                self.assertLess(time.monotonic() - started, .25)
                pipeline = window.export_pipeline
                self.assertTrue(window.exporting); self.assertEqual(window.progress.maximum(), 0)
                ticks.clear(); timer.start()
                wait_until(lambda: window.state_label.text() == 'phase-20', app=self.app)
                self.assertEqual(window.progress.value(), 20)
                self.assertFalse(window.blend_button.isEnabled())
                window.nav_group.button(1).click(); self.app.processEvents()
                self.assertEqual(window.pages.currentIndex(), 1)
                window.nav_group.button(0).click()
                if cancelled: window.stop_button.click()
                else:
                    wait_until(lambda: window.state_label.text() == 'phase-70', app=self.app)
                    self.assertGreater(len(ticks), 10)
                    self.assertFalse(output.exists())
                wait_until(lambda: not window.exporting, app=self.app); timer.stop()
                self.assertEqual(pipeline.callback, window.events.put)
                self.assertIsNone(pipeline.process)
                self.assertEqual(output.exists(), not cancelled)
                self.assertTrue(window.blend_button.isEnabled())
                self.assertEqual(window.progress.maximum(), 5)
                self.assertEqual(window.progress.value(), 0 if cancelled else 5)
                self.assertIn('已停止' if cancelled else '已生成', window.state_label.text())

    def test_blender_point_size_default_migration(self):
        from blender_export import default_point_radius, progress_event
        self.assertEqual(default_point_radius({}), .005)
        self.assertEqual(default_point_radius({'blender_point_radius': .01}), .005)
        self.assertEqual(default_point_radius({'blender_point_radius': .05}), .005)
        self.assertEqual(default_point_radius({'blender_point_radius': .002}), .002)
        self.assertEqual(default_point_radius({'blender_point_radius': .01, 'blender_point_size_revision': 2}), .01)
        self.assertIsNone(progress_event('ordinary log line'))
        self.assertIsNone(progress_event('[BLENDER_PROGRESS] invalid'))
        self.assertIsNone(progress_event('[BLENDER_PROGRESS] {}'))
        self.assertEqual(progress_event('[BLENDER_PROGRESS] {"text":"save", "percent":100}')['percent'], 99)

    def test_ffmpeg_scale_and_legacy_cache_identity(self):
        options = Options(output_root=self.temp.name, fps=12, image_scale=.5, cuda_decode=False)
        pipeline = Pipeline(options)
        scene = pipeline.prepare_video(self.video)
        info = video_info(scene / '_cache/images/frame_000001.jpg', 0)
        self.assertEqual((info['width'], info['height']), (320, 180))
        self.assertEqual(len(list((scene / '_cache/images').glob('*.jpg'))), 12)
        original = replace(options, image_scale=1)
        signature = source_signature(self.video, original)
        self.assertNotIn('image_scale', signature)
        self.assertNotIn('image_scale', settings_signature(original))
        self.assertNotEqual(video_scene(self.video, options), video_scene(self.video, original))
        self.assertEqual(video_filters(options, original_size=True), 'fps=12')

    def test_named_project_cache_resume_and_no_collision(self):
        options = Options(output_root=self.temp.name, fps=12, image_scale=.5, cuda_decode=False)
        first = Pipeline(options); first.project_name = '镜头 11'
        scene = first.prepare_video(self.video)
        self.assertEqual(scene.name, '镜头 11')
        self.assertEqual({p.name for p in scene.iterdir()}, {'_cache', 'log'})
        marker = scene / '_cache/.frames-complete'
        timestamp = marker.stat().st_mtime_ns
        resumed = Pipeline(options); resumed.project_name = '镜头 11'; resumed.project_dir = scene
        self.assertEqual(resumed.prepare_video(self.video), scene)
        self.assertEqual(marker.stat().st_mtime_ns, timestamp)
        second = Pipeline(options); second.project_name = '镜头 11'
        self.assertEqual(second.prepare_video(self.video).name, '镜头 11 (2)')
        changed = Pipeline(replace(options, image_scale=.8)); changed.project_name = '镜头 11'; changed.project_dir = scene
        self.assertNotEqual(changed.prepare_video(self.video), scene)
        renamed = Pipeline(options); renamed.project_name = '镜头 12'; renamed.project_dir = scene
        self.assertEqual(renamed.prepare_video(self.video).name, '镜头 12')
        relocated = Pipeline(replace(options, output_root=str(Path(self.temp.name) / 'new-root')))
        relocated.project_name = '镜头 11'; relocated.project_dir = scene
        self.assertEqual(relocated.prepare_video(self.video).parent.name, 'new-root')
        self.assertEqual(safe_name('../CON:*'), '.._CON__')
        self.assertEqual(safe_name('NUL.txt'), '_NUL.txt')
        self.assertEqual(blend_filename('镜头 11', 'Blender 4.2.15 LTS'), '镜头 11_4.2.blend')

    def test_new_and_legacy_projects_scan_move_export_and_archive(self):
        root = Path(self.temp.name)
        project = create_project(root, '镜头 11')
        model = project / 'point could'; model.mkdir()
        for name in ('cameras.txt', 'images.txt', 'points3D.txt'):
            (model / name).write_text('# header\n1 2 3\n', encoding='utf-8')
        (project / '镜头 11_4.2.blend').write_bytes(b'blend data')
        (project / '_cache/database.db').write_bytes(b'cache excluded' * 1000)
        result = dict(layout_version=2, project_dir=str(project), name='镜头 11', model=str(model),
                      scene=str(project), source=str(self.video), status='complete', metrics={'points': 1},
                      completed_at='2026-10-24T11:00:00')
        (project / '_cache/result.json').write_text(json.dumps(result), encoding='utf-8')
        moved = root / '移过位置'; shutil.move(str(project), str(moved))
        legacy = root / 'legacy/results/fastmap/20261024_100000_123456'; (legacy / 'sparse/0').mkdir(parents=True)
        old = dict(result, model=str(legacy / 'sparse/0'), scene=str(root / 'legacy'))
        old.pop('layout_version'); old.pop('project_dir')
        (legacy / 'result.json').write_text(json.dumps(old), encoding='utf-8')
        library = ProjectLibrary(root / 'library.json')
        records, warnings = library.scan(root)
        local = [r for r in records if Path(r['directory']).is_relative_to(root)]
        self.assertFalse(warnings)
        self.assertEqual(len(local), 2)
        new = next(r for r in local if r['ready'])
        self.assertEqual(new['directory'], str(moved.resolve()))
        self.assertEqual(new['result']['model'], str(moved / 'point could'))
        self.assertEqual(new['result']['images_dir'], str(moved / '_cache/images'))
        self.assertEqual(len(new['exports']), 1)
        self.assertLess(new['bytes'], 1000)
        archived = library.archive(new)
        self.assertTrue((archived / '_cache/database.db').is_file())
        self.assertTrue((archived / '_cache/recovery.json').is_file())
        self.assertTrue(self.video.is_file())
        self.assertTrue((legacy / 'result.json').is_file())

    def test_search_toolbar_and_settings_alignment(self):
        window = self.window
        window.search.setText('11'); self.app.processEvents()
        for control in (window.sort_box, window.refresh_button, window.filters.button(0).parentWidget()):
            self.assertEqual(control.height(), window.search.height())
            self.assertEqual(control.mapTo(window, control.rect().center()).y(),
                             window.search.mapTo(window, window.search.rect().center()).y())
        window.grab().save(str(self.artifacts / 'workspace-aligned.png'))
        window.nav_group.button(1).click(); self.app.processEvents()
        control = window.default_controls['gpu_min_images']
        form = control.parentWidget().layout(); title = form.labelForField(control)
        self.assertGreaterEqual(control.x() - title.geometry().right(), 32)
        window.grab().save(str(self.artifacts / 'settings-aligned.png'))

    def test_dialog_metadata_validation_and_actual_options(self):
        dialog = TaskDialog(self.window, Options(), paths=[self.video]); dialog.show()
        wait_until(lambda: bool(dialog.metadata), app=self.app)
        self.assertFalse(dialog.rate.model().item(1).isEnabled())
        self.assertFalse(dialog.rate.model().item(2).isEnabled())
        dialog.rate.setCurrentIndex(2); dialog.accept_task()
        self.assertIsNone(dialog.result_data)
        dialog.rate.setCurrentIndex(3); dialog.custom_rate.setValue(12); dialog.scale.setCurrentIndex(2)
        dialog.sequence.setChecked(True); dialog.sequence_size.setCurrentIndex(1)
        dialog.accept_task()
        self.assertEqual(dialog.result_data['options'].fps, 12)
        self.assertEqual(dialog.result_data['options'].image_scale, .5)
        self.assertTrue(dialog.result_data['options'].export_sequence)
        self.assertTrue(dialog.result_data['options'].sequence_original)
        dialog.hide(); dialog.deleteLater()

    def test_unified_history_filters_persistence_and_responsive_ui(self):
        records, _ = ProjectLibrary().scan(str(ROOT / 'outputs'))
        self.window.records = {r['id']: r for r in records}
        iid = self.window.tasks.add(self.video, Options(fps=12, image_scale=.5), '回归测试 · ' + '长名称' * 30)
        self.window.rebuild_rows(); self.window.table.selectRow(0); self.app.processEvents()
        self.assertTrue(self.window.edit_button.isEnabled())
        self.assertEqual(self.window.source_path.text(), str(self.video.resolve()))
        self.window.filters.button(1).setChecked(True); self.window.apply_filter()
        self.assertEqual(self.window.proxy.rowCount(), 1)
        self.window.search.setText('不存在的关键词'); self.app.processEvents(); self.assertEqual(self.window.proxy.rowCount(), 0)
        self.window.search.clear(); self.window.filters.button(0).setChecked(True); self.window.apply_filter()
        self.window.persist_tasks()
        saved = json.loads((Path(self.temp.name) / 'task-queue.json').read_text(encoding='utf-8'))
        self.assertEqual(saved[0]['options']['image_scale'], .5)
        self.window.tasks.jobs.clear(); self.window.restore_tasks(); self.assertIn(iid, self.window.tasks.jobs)
        ticks = []; timer = QTimer(); timer.setInterval(10); timer.timeout.connect(lambda: ticks.append(1)); timer.start()
        finished = threading.Event()
        threading.Thread(target=lambda: (time.sleep(.25), finished.set()), daemon=True).start()
        wait_until(finished.is_set, app=self.app); timer.stop(); self.assertGreater(len(ticks), 5)
        self.window.resize(1020, 680); self.app.processEvents(); self.assertLessEqual(self.window.width(), 1020)
        self.window.grab().save(str(self.artifacts / 'workspace-small.png'))

    def test_logs_and_three_navigation_pages(self):
        for index in range(3):
            self.window.nav_group.button(index).click(); self.app.processEvents(); self.assertEqual(self.window.pages.currentIndex(), index)
        self.window.append_log('一条长日志 ' * 50000)
        self.assertLessEqual(self.window.log_chars, 131072)
        self.window.open_logs(); self.app.processEvents(); self.assertTrue(self.window.log_dialog)
        self.window.log_dialog.close(); self.app.processEvents()
        # DeferredDelete is processed when the normal event loop runs.
        QTimer.singleShot(30, self.app.quit); self.app.exec(); self.assertIsNone(self.window.log_widget)

    def test_created_sort_output_size_and_real_row_action_click(self):
        first = self.window.tasks.add(self.video, Options(), '较早任务')
        second = self.window.tasks.add(self.video, Options(), '最新任务')
        self.window.tasks.jobs[first]['created_at'] = '2026-10-23T10:00:00'
        self.window.tasks.jobs[second]['created_at'] = '2026-10-24T10:00:00'
        self.window.rebuild_rows(); self.app.processEvents()
        self.assertEqual(self.window.sort_box.currentText(), '执行顺序')
        self.window.sort_box.setCurrentIndex(self.window.sort_box.findText('最新创建'))
        self.assertEqual(self.window.sort_box.currentText(), '最新创建')
        self.assertEqual(self.window.proxy.index(0, 0).data(Qt.ItemDataRole.UserRole)['id'], second)
        self.window.sort_box.setCurrentIndex(self.window.sort_box.findText('最早创建')); self.app.processEvents()
        self.assertEqual(self.window.sort_box.currentText(), '最早创建')
        self.assertEqual(self.window.proxy.index(0, 0).data(Qt.ItemDataRole.UserRole)['id'], first)
        self.window.sort_box.setCurrentIndex(self.window.sort_box.findText('最新创建'))
        index = self.window.proxy.index(0, 6)
        rect = self.window.table.visualRect(index)
        point = QPoint(rect.left() + 6 + 4 * 26 + 13, rect.center().y())
        QTest.mouseClick(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()
        self.assertNotIn(second, self.window.tasks.jobs)
        self.assertIn(first, self.window.tasks.jobs)
        records, _ = ProjectLibrary().scan(str(ROOT / 'outputs'))
        self.assertTrue(all(record['bytes'] > 0 for record in records))
        self.assertTrue(all(record['created'] for record in records))

    def test_priority_arrow_clicks_persistence_and_start_all_with_selection(self):
        window = self.window
        a, b, c = [window.tasks.add(self.video, Options(), name) for name in ('任务 A', '任务 B', '任务 C')]
        window.rebuild_rows(); self.app.processEvents()
        self.assertFalse(window.priority_up.isEnabled()); self.assertFalse(window.priority_down.isEnabled())
        window.sort_box.setCurrentIndex(window.sort_box.findText('最新创建'))
        def select(iid):
            index = next(i for i in range(window.proxy.rowCount()) if window.proxy.index(i, 0).data(Qt.ItemDataRole.UserRole)['id'] == iid)
            window.table.selectRow(index); self.app.processEvents()
        select(c)
        QTest.mouseClick(window.priority_up, Qt.MouseButton.LeftButton); self.app.processEvents()
        self.assertEqual(list(window.tasks.snapshot()), [a, c, b])
        self.assertEqual(window.sort_box.currentText(), '执行顺序')
        self.assertEqual([window.proxy.index(i, 0).data(Qt.ItemDataRole.UserRole)['id'] for i in range(3)], [a, c, b])
        self.assertEqual(window.selected()['id'], c)
        QTest.mouseClick(window.priority_up, Qt.MouseButton.LeftButton); self.app.processEvents()
        self.assertEqual(list(window.tasks.snapshot()), [c, a, b])
        self.assertFalse(window.priority_up.isEnabled()); self.assertTrue(window.priority_down.isEnabled())
        QTest.mouseClick(window.priority_down, Qt.MouseButton.LeftButton); self.app.processEvents()
        self.assertEqual(list(window.tasks.snapshot()), [a, c, b])
        window.tasks.jobs.clear(); window.restore_tasks(); window.rebuild_rows(); select(c)
        self.assertEqual(list(window.tasks.snapshot()), [a, c, b])
        self.assertIn('第 2 项', window.detail_info.text())
        with patch.object(window.tasks, 'start', return_value=True) as start:
            QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
            start.assert_called_once_with(None)
        with patch.object(window.tasks, 'start', return_value=True) as start:
            window.row_action(window.selected(), 'play')
            start.assert_called_once_with([c])
        window.grab().save(str(self.artifacts / 'workspace-priority.png'))
        for width in (1020, 1360):
            window.resize(width, 800); self.app.processEvents()
            for control in (window.priority_up, window.priority_down, window.refresh_button):
                self.assertEqual(control.height(), window.search.height())
                self.assertLess(control.geometry().right(), window.width())

    def test_column_boundary_tracks_mouse_in_both_directions(self):
        header = self.window.table.horizontalHeader()
        self.app.processEvents()
        for section in (1, 2, 3, 4, 5):
            for delta in (35, -20):
                original_name_width = header.sectionSize(0)
                original_width = header.sectionSize(section)
                original_boundary = header.sectionViewportPosition(section) + original_width
                start = QPoint(original_boundary - 1, header.height() // 2)
                end = start + QPoint(delta, 0)
                QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=start)
                QTest.mouseMove(header.viewport(), end, 25)
                QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=end)
                self.app.processEvents()
                boundary = header.sectionViewportPosition(section) + header.sectionSize(section)
                self.assertEqual(header.sectionSize(section), original_width + delta)
                self.assertEqual(boundary, original_boundary + delta)
                self.assertEqual(header.sectionSize(0), original_name_width)
        start = QPoint(header.sectionSize(0) - 1, header.height() // 2)
        end = start + QPoint(45, 0)
        QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(header.viewport(), end, 25)
        QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        chosen_name_width = header.sectionSize(0)
        chosen_settings_width = header.sectionSize(2)
        self.assertEqual(chosen_name_width, original_name_width + 45)

    def test_window_resize_refits_after_manual_column_drag(self):
        table = self.window.table; header = table.horizontalHeader()
        for number in range(24):
            self.window.tasks.add(self.video, Options(), f'窗口缩放测试 {number + 1}')
        self.window.rebuild_rows(); self.app.processEvents()
        for section, delta in ((0, -80), (2, 25)):
            start = QPoint(header.sectionViewportPosition(section) + header.sectionSize(section) - 1, header.height() // 2)
            end = start + QPoint(delta, 0)
            original = header.sectionSize(section)
            QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(header.viewport(), end, 25)
            QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=end)
            self.app.processEvents()
            self.assertEqual(header.sectionSize(section), original + delta)
        chosen_settings_width = header.sectionSize(2)

        def assert_fitted():
            self.app.processEvents()
            used = sum(table.columnWidth(column) for column in range(1, self.window.model.columnCount())
                       if not table.isColumnHidden(column))
            self.assertEqual(table.columnWidth(0), max(220, table.viewport().width() - used))
            self.assertEqual(header.sectionSize(2), chosen_settings_width)
            self.assertEqual(header.sectionSize(6), 142)
            if table.viewport().width() >= used + 220:
                self.assertEqual(header.length(), table.viewport().width())
                self.assertEqual(table.horizontalScrollBar().maximum(), 0)

        for width in (1550, 1700, 1200, 1190, 1020, 1360):
            self.window.resize(width, 800); self.app.processEvents()
            assert_fitted()
        self.window.showMaximized(); assert_fitted()
        self.window.showNormal(); assert_fitted()
        self.window.grab().save(str(self.artifacts / 'workspace-resizing.png'))


if __name__ == '__main__': unittest.main(verbosity=2)
