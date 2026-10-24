"""UI-independent serial task scheduling; solvers remain in tracker_core.Pipeline."""
from dataclasses import asdict
import copy
import threading
import traceback
import uuid
from datetime import datetime

from tracker_core import Options, Pipeline, Cancelled
from estimate import save_sample
from project_layout import result_directory, blend_filename

MOVABLE_STATES = ('等待', '排队', '失败', '已停止')


class TaskQueue:
    def __init__(self, emit, pipeline_factory=Pipeline, sample_writer=save_sample):
        self.emit = emit
        self.pipeline_factory = pipeline_factory
        self.sample_writer = sample_writer
        self.jobs = {}
        self.condition = threading.Condition()
        self.cancelled = threading.Event()
        self.pipeline = None
        self.running = False
        self.editing = None
        self.paused = False
        self.pending_ids = set()
        self.accepting = False

    def add(self, path, options, name=None, scene=False, auto_blender=False):
        from pathlib import Path
        iid = uuid.uuid4().hex
        with self.condition:
            self.jobs[iid] = dict(id=iid, path=str(Path(path).resolve()), name=name or Path(path).stem,
                                  scene=scene, options=asdict(options), state='等待', progress=0,
                                  auto_blender=auto_blender, created_at=datetime.now().isoformat(timespec='seconds'))
            if self.running and self.accepting:
                self.jobs[iid]['state'] = '排队'
                self.pending_ids.add(iid)
        return iid

    def snapshot(self):
        with self.condition:
            return copy.deepcopy(self.jobs)

    def _moved_order(self, ids, direction):
        if direction not in (-1, 1): return None
        order = [iid for iid, job in self.jobs.items() if job['state'] in MOVABLE_STATES]
        selected = set(ids)
        if not selected or not selected.issubset(order): return None
        original = order[:]
        indices = range(1, len(order)) if direction == -1 else range(len(order) - 2, -1, -1)
        for index in indices:
            neighbor = index + direction
            if order[index] in selected and order[neighbor] not in selected:
                order[index], order[neighbor] = order[neighbor], order[index]
        return order if order != original else None

    def can_move(self, ids, direction):
        with self.condition:
            return self._moved_order(ids, direction) is not None

    def move(self, ids, direction):
        with self.condition:
            order = self._moved_order(ids, direction)
            if order is None: return False
            movable = set(order); reordered = iter(order)
            keys = [next(reordered) if iid in movable else iid for iid in self.jobs]
            self.jobs = {iid: self.jobs[iid] for iid in keys}
            return True

    def begin_edit(self, iid):
        with self.condition:
            job = self.jobs[iid]
            if self.editing or job['state'] in ('运行中', '完成'):
                return None
            self.editing = iid
            return copy.deepcopy(job)

    def end_edit(self, updates=None):
        with self.condition:
            if updates is not None and self.editing in self.jobs:
                self.jobs[self.editing].update(updates)
            self.editing = None
            self.condition.notify_all()

    def remove(self, iid):
        with self.condition:
            if self.jobs[iid]['state'] in ('运行中', '排队') or iid == self.editing:
                return False
            del self.jobs[iid]
            return True

    def start(self, ids=None):
        with self.condition:
            if self.running or self.editing:
                return False
            selected = set(ids) if ids is not None else set(self.jobs)
            ids = [iid for iid, job in self.jobs.items() if iid in selected and job['state'] in ('等待', '失败', '已停止')]
            if not ids:
                return False
            # A delayed cancel thread still owns the previous run's event.
            self.cancelled = threading.Event()
            self.running = True
            self.paused = False
            self.pending_ids = set(ids)
            self.accepting = True
            for iid in ids:
                self.jobs[iid].update(state='排队', progress=0, error='')
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _pipeline_event(self, iid, event):
        with self.condition:
            job = self.jobs[iid]
            if event['type'] == 'scene':
                job['project_dir'] = event['path']
            elif event['type'] == 'solver_warning':
                warnings = job['solver_warnings']
                if not any(item['code'] == event['code'] for item in warnings):
                    warnings.append({field: event[field] for field in ('code', 'text', 'log_path')})
        self.emit({**event, 'iid': iid})

    def _create_pipeline(self, iid, job):
        pipeline = self.pipeline_factory(Options(**job['options']), lambda event: self._pipeline_event(iid, event))
        pipeline.cancelled = self.cancelled
        pipeline.project_name = job['name']
        pipeline.project_dir = job.get('project_dir')
        with self.condition:
            self.pipeline = pipeline
        return pipeline

    def _diagnostic(self, pipeline, iid, text):
        if isinstance(pipeline, Pipeline):
            try:
                pipeline.emit('log', text=text)
                return
            except OSError as error:
                text = f'[任务日志写入失败] {error}\n{text}'
        self.emit({'type': 'log', 'iid': iid, 'text': text})

    def _run(self):
        failed = processed = 0
        try:
            while True:
                with self.condition:
                    while self.pending_ids and (self.editing or self.paused) and not self.cancelled.is_set():
                        self.emit({'type': 'queue_wait'})
                        self.condition.wait()
                    if self.cancelled.is_set():
                        raise Cancelled('队列已停止；已完成的缓存保留。')
                    iid = next((key for key in self.jobs if key in self.pending_ids), None)
                    if iid is None:
                        self.accepting = False
                        break
                    self.pending_ids.remove(iid)
                    job = self.jobs[iid]
                    self.pipeline = None
                    job.update(state='运行中', solver_warnings=[], export_error='', stage='')
                    job.pop('result', None)
                    path, scene = job['path'], job['scene']
                    processed += 1
                pipeline = None
                self.emit({'type': 'job', 'iid': iid, 'state': '运行中'})
                try:
                    pipeline = self._create_pipeline(iid, job)
                    if self.cancelled.is_set():
                        raise Cancelled('任务已停止。')
                    result = pipeline.run(path, existing_scene=scene)
                    with self.condition:
                        job['result'] = result
                    try:
                        self.sample_writer(result)
                    except Exception as error:
                        # Timing history is optional; the completed reconstruction owns its success.
                        self.emit({'type': 'log', 'iid': iid, 'text': f'[耗时预估记录] {error}\n'})
                    blender = job.get('blender')
                    if blender:
                        from pathlib import Path
                        from blender_export import export_blender
                        output = result_directory(result) / blend_filename(job['name'], blender['version'])
                        if output.exists():
                            output = output.with_stem(output.stem + '_' + uuid.uuid4().hex[:6])
                        self.emit({'type': 'stage', 'iid': iid, 'index': 5, 'total': 5, 'text': '生成 Blender 场景'})
                        try:
                            saved = export_blender(pipeline, result, output, blender)
                            self.emit({'type': 'blender_saved', 'model': result['model'], 'output': saved, 'blender': blender})
                        except Cancelled:
                            raise
                        except Exception as error:
                            with self.condition:
                                job['export_error'] = str(error)
                            self._diagnostic(pipeline, iid, f'[Blender 导出失败，点云已保留] {error}\n')
                    with self.condition:
                        job.update(state='完成', progress=5, result=result)
                    self.emit({'type': 'job', 'iid': iid, 'state': '完成', 'result': result})
                except Cancelled:
                    self._diagnostic(pipeline, iid, '[停止] 任务已停止，缓存保留。\n')
                    with self.condition:
                        job['state'] = '已停止'
                    self.emit({'type': 'job', 'iid': iid, 'state': '已停止'})
                    raise
                except Exception as error:
                    failed += 1
                    with self.condition:
                        job.update(state='失败', error=str(error))
                    self._diagnostic(pipeline, iid, traceback.format_exc())
                    self.emit({'type': 'job', 'iid': iid, 'state': '失败'})
            message = f'队列处理完成 · {processed - failed} 项成功' + (f' · {failed} 项失败' if failed else '')
        except Cancelled as error:
            message = str(error)
        except Exception as error:
            message = f'队列中断：{error}'
            self.emit({'type': 'log', 'text': traceback.format_exc()})
        finally:
            with self.condition:
                for job in self.jobs.values():
                    if job['state'] in ('排队', '运行中'):
                        job['state'] = '等待'
                self.pipeline = None
                self.running = False
                self.accepting = False
                self.pending_ids.clear()
            self.emit({'type': 'finished', 'text': message})

    def pause(self, value):
        with self.condition:
            self.paused = value
            self.condition.notify_all()

    def cancel(self):
        with self.condition:
            self.cancelled.set()
            self.accepting = False
            pipeline = self.pipeline
            self.condition.notify_all()
        if pipeline:
            threading.Thread(target=pipeline.cancel, daemon=True).start()
