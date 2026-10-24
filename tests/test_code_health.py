"""Regression coverage for failure isolation, recovery, and process ownership."""
from dataclasses import asdict
from pathlib import Path
import json
import os
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from tracker_core import Options, Pipeline, Cancelled
from task_queue import TaskQueue
from project_library import ProjectLibrary
from qt_app import MainWindow, configure_application
from PySide6.QtWidgets import QApplication


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline: raise AssertionError('operation timed out')
        time.sleep(.01)


class QueueHealthTests(unittest.TestCase):
    def test_unwritable_task_log_does_not_abort_remaining_jobs(self):
        events = []
        class Solver(Pipeline):
            def run(self, path, existing_scene=False):
                if Path(path).stem == 'bad': raise ValueError('solver error')
                return {'status': 'complete'}
            def emit(self, kind, **data): raise OSError('disk full')
        tasks = TaskQueue(events.append, Solver, lambda result: None)
        first = tasks.add('bad.mp4', Options()); second = tasks.add('good.mp4', Options())
        tasks.start(); wait_until(lambda: not tasks.running)
        self.assertEqual(tasks.jobs[first]['state'], '失败')
        self.assertEqual(tasks.jobs[second]['state'], '完成')
        self.assertTrue(any('disk full' in event.get('text', '') for event in events))

    def test_statistics_failure_does_not_fail_solve_or_skip_export(self):
        with tempfile.TemporaryDirectory() as directory:
            result = dict(status='complete', project_dir=directory, model=str(Path(directory) / 'point could'))
            class Solver:
                def __init__(self, options, callback): pass
                def run(self, path, existing_scene=False): return result
            tasks = TaskQueue(lambda event: None, Solver, lambda result: (_ for _ in ()).throw(ValueError('bad history')))
            iid = tasks.add('test.mp4', Options())
            tasks.jobs[iid]['blender'] = dict(version='Blender 5.2')
            with patch('blender_export.export_blender', return_value=str(Path(directory) / 'test.blend')) as exported:
                tasks.start(); wait_until(lambda: not tasks.running)
                self.assertEqual(tasks.jobs[iid]['state'], '完成')
                exported.assert_called_once()

    def test_factory_failure_is_local_and_retry_resets_old_errors(self):
        calls = []; states = []
        class Solver:
            def __init__(self, options, callback):
                calls.append(options.fps)
                if options.fps == 1: raise ValueError('bad solver setup')
            def run(self, path, existing_scene=False):
                states.append(tasks.snapshot()[second])
                return {'status': 'complete'}
        tasks = TaskQueue(lambda event: None, Solver, lambda result: None)
        first = tasks.add('bad.mp4', Options(fps=1))
        second = tasks.add('good.mp4', Options(fps=2))
        tasks.jobs[second].update(state='失败', export_error='old export error', result={'old': True}, stage='old stage')
        tasks.start(); wait_until(lambda: not tasks.running)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(tasks.jobs[first]['state'], '失败')
        self.assertEqual(tasks.jobs[second]['state'], '完成')
        self.assertFalse(states[0].get('export_error'))
        self.assertFalse(states[0].get('result'))
        self.assertFalse(states[0].get('stage'))

    def test_delayed_stop_cannot_cancel_next_run(self):
        cancel_entered = threading.Event(); release_cancel = threading.Event(); cancel_done = threading.Event()
        second_running = threading.Event(); release_second = threading.Event(); calls = []
        class Solver:
            def __init__(self, options, callback): pass
            def run(self, path, existing_scene=False):
                calls.append(path)
                if len(calls) == 1:
                    self.cancelled.wait(3)
                    raise Cancelled('stopped')
                second_running.set(); release_second.wait(3)
                if self.cancelled.is_set(): raise Cancelled('unexpected stop')
                return {'status': 'complete'}
            def cancel(self):
                cancel_entered.set(); release_cancel.wait(3)
                self.cancelled.set(); cancel_done.set()
        tasks = TaskQueue(lambda event: None, Solver, lambda result: None)
        iid = tasks.add('test.mp4', Options())
        try:
            tasks.start(); wait_until(lambda: tasks.pipeline is not None)
            tasks.cancel(); self.assertTrue(cancel_entered.wait(2)); wait_until(lambda: not tasks.running)
            tasks.start(); self.assertTrue(second_running.wait(2))
            release_cancel.set(); self.assertTrue(cancel_done.wait(2))
            release_second.set(); wait_until(lambda: not tasks.running)
            self.assertEqual(tasks.jobs[iid]['state'], '完成')
        finally:
            release_cancel.set(); release_second.set()

    def test_stream_error_reaps_own_child(self):
        with tempfile.TemporaryDirectory() as directory:
            children = []
            def receive(event):
                if event['type'] == 'log' and event['text'].strip() == 'ready':
                    children.append(pipeline.process)
                    raise OSError('log disk failed')
            pipeline = Pipeline(Options(), receive); pipeline.log_dir = Path(directory)
            try:
                with self.assertRaises(OSError):
                    pipeline._command([sys.executable, '-u', '-c', 'import time; print("ready",flush=True); time.sleep(30)'], 'stream-failure')
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].poll())
                self.assertIsNone(pipeline.process)
            finally:
                for child in children:
                    if child.poll() is None: child.kill(); child.wait()

    def test_statistics_accept_task_time_without_mapping_time(self):
        import estimate
        with tempfile.TemporaryDirectory() as directory, patch.object(estimate, 'HISTORY', Path(directory) / 'history.json'):
            estimate.save_sample(dict(backend='glomap-gpu', database={'images': 20}, task_wall_seconds=4))
            self.assertEqual(estimate.read_samples()[0]['seconds'], 4)


class RecoveryHealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([]); configure_application(cls.app)

    def test_bad_entry_does_not_hide_later_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = dict(id='valid', path='video.mp4', name='valid', options=asdict(Options()), state='等待', scene=False)
            bad = {**valid, 'id': 'bad', 'options': {'unknown_option': True}}
            bad_result = {**valid, 'id': 'bad-result', 'result': {'metrics': {'points': 'many'}}}
            Path(directory, 'task-queue.json').write_text(json.dumps([bad, bad_result, valid]), encoding='utf-8')
            window = MainWindow(config_dir=directory, background=False)
            try: self.assertEqual(list(window.tasks.jobs), ['valid'])
            finally: window.poller.stop(); window.deleteLater(); self.app.processEvents()

    def test_wrong_settings_root_does_not_prevent_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'settings.json').write_text('[]', encoding='utf-8')
            window = MainWindow(config_dir=directory, background=False)
            try: self.assertEqual(window.defaults.backend, 'fastmap')
            finally: window.poller.stop(); window.deleteLater(); self.app.processEvents()

    def test_library_ignores_bad_nested_paths_and_keeps_valid_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'library.json'
            path.write_text(json.dumps({'roots': [None, directory], 'exports': {'model': [None, 'out.blend']}}), encoding='utf-8')
            library = ProjectLibrary(path)
            self.assertIn(str(Path(directory)), library.data['roots'])
            self.assertEqual(len(next(iter(library.data['exports'].values()))), 1)


class BoundaryHealthTests(unittest.TestCase):
    def test_options_reject_invalid_types_and_nonfinite_values(self):
        for values in ({'fps': float('nan')}, {'max_size': '4096'}, {'glomap_gpu': 'false'},
                       {'output_root': ''}, {'gpu_min_images': 1}, {'image_scale': 2}):
            with self.subTest(values=values), self.assertRaises(ValueError): Options(**values).validate()

    def test_atomic_json_failure_keeps_previous_record_and_cleans_temp(self):
        from json_store import atomic_write_json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            atomic_write_json(path, {'valid': True})
            with self.assertRaises(ValueError): atomic_write_json(path, {'invalid': float('nan')})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'valid': True})
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_desktop_import_does_not_load_legacy_tk(self):
        code = 'import sys; sys.path.insert(0, "app"); import qt_app; assert "tkinter" not in sys.modules; assert "AutoTracker_GUI" not in sys.modules'
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_blender_probe_rejects_unsupported_node_interface(self):
        from blender_runtime import probe_blender
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'blender.exe'; path.touch()
            for version, supported in (('3.6.0', False), ('4.2.15', True), ('5.2.0', True)):
                with patch('blender_runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 0, f'Blender {version}\n', '')):
                    if supported: self.assertIn(version, probe_blender(path)['version'])
                    else:
                        with self.assertRaises(ValueError): probe_blender(path)


if __name__ == '__main__': unittest.main(verbosity=2)
